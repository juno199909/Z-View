# -*- coding: utf-8 -*-
"""1.9.56 专项 2：自 cmdb_agent_core.py 逐字迁入（#16/#10 模块化，逻辑未改）。"""
import hashlib
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import requests

from zvagent.auth import _agent_requests_verify, _software_headers
from zvagent.config import SOFTWARE_CONFIG

class SoftwareManager:
    def __init__(self, asset_id: int):
        self.asset_id = asset_id
        self.running = False
        self.thread: threading.Thread | None = None
        self.download_dir = Path(SOFTWARE_CONFIG.get("download_dir", r"C:\CMDB-Agent\Downloads"))
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._loop, name="software-manager", daemon=True)
        self.thread.start()
        print(f"[SoftwareManager] 启动 (asset_id={self.asset_id})")

    def stop(self):
        self.running = False

    def _loop(self):
        policy_interval = SOFTWARE_CONFIG.get("intervals", {}).get("policy_sync", 300)
        task_interval = SOFTWARE_CONFIG.get("intervals", {}).get("task_poll", 30)
        last_policy_sync = 0.0

        while self.running:
            now = time.time()

            # 策略同步
            if now - last_policy_sync >= policy_interval:
                try:
                    self._sync_policies()
                    last_policy_sync = now
                except Exception as exc:
                    print(f"[SoftwareManager] 策略同步失败: {exc}")

            # 任务轮询
            try:
                self._poll_and_execute_tasks()
            except Exception as exc:
                print(f"[SoftwareManager] 任务轮询失败: {exc}")

            time.sleep(task_interval)

    def _sync_policies(self):
        url = urljoin(SOFTWARE_CONFIG["server_url"], "/api/v1/software/agent/policies")
        payload = {"asset_id": self.asset_id}
        resp = requests.post(url, json=payload, headers=_software_headers(), timeout=30, verify=_agent_requests_verify())
        if resp.status_code == 200:
            print("[SoftwareManager] 策略同步成功")
        else:
            print(f"[SoftwareManager] 策略同步 HTTP {resp.status_code}")

    def _poll_and_execute_tasks(self):
        url = urljoin(SOFTWARE_CONFIG["server_url"], "/api/v1/software/agent/tasks/poll")
        payload = {"asset_id": self.asset_id}
        resp = requests.post(url, json=payload, headers=_software_headers(), timeout=30, verify=_agent_requests_verify())
        if resp.status_code != 200:
            return

        data = resp.json()
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        if not tasks:
            return

        for task in tasks:
            try:
                self._execute_task(task)
            except Exception as exc:
                print(f"[SoftwareManager] 任务执行失败: {exc}")
                self._report_task_result(task.get("result_id") or task.get("id"), "failed", str(exc))

    def _execute_task(self, task: dict):
        # 后端当前下发 task_id/result_id/package_info；保留旧字段兼容历史接口。
        result_id = task.get("result_id") or task.get("id")
        task_id = task.get("task_id") or task.get("id")
        task_type = task.get("task_type") or task.get("type") or "install"
        package_info = task.get("package_info") or {}
        package_id = package_info.get("id") or task.get("package_id")

        print(
            f"[SoftwareManager] 执行任务: task_id={task_id} result_id={result_id} "
            f"type={task_type} package={package_id}"
        )

        if not result_id:
            print("[SoftwareManager] 任务缺少 result_id，无法回传执行结果")
            return

        if task_type in ("install", "upgrade") and package_id:
            self._do_install(result_id, task_id, int(package_id), task, package_info)
        elif task_type == "uninstall" and package_id:
            self._do_uninstall(result_id, task_id, int(package_id), task, package_info)
        else:
            self._report_task_result(result_id, "failed", f"unsupported_task_type={task_type}")

    def _do_install(self, result_id, task_id, package_id: int, task: dict, package_info: dict):
        # 下载软件包
        download_url = urljoin(
            SOFTWARE_CONFIG["server_url"],
            f"/api/v1/software/agent/packages/{package_id}/download?asset_id={self.asset_id}",
        )
        file_name = self._safe_file_name(package_info.get("file_name") or f"package_{package_id}.bin")
        file_path = self.download_dir / file_name
        expected_hash = str(package_info.get("file_hash") or "").strip()

        max_retries = SOFTWARE_CONFIG.get("max_retries", 3)
        for attempt in range(max_retries):
            try:
                self._report_task_result(
                    result_id,
                    "downloading",
                    f"开始下载软件包 task_id={task_id} package_id={package_id}",
                    progress=0,
                    download_progress=0,
                )
                self._download_file(download_url, file_path, result_id, expected_hash)
                break
            except Exception as exc:
                print(f"[SoftwareManager] 下载失败 (attempt {attempt + 1}): {exc}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)

        # 执行安装
        install_cmd = package_info.get("install_command") or task.get("install_command")
        install_cmd = self._build_package_command(install_cmd, file_path)
        print(f"[SoftwareManager] 执行安装: {install_cmd}")
        self._report_task_result(
            result_id,
            "installing",
            f"开始执行安装命令 task_id={task_id}",
            progress=80,
            download_progress=100,
            install_progress=0,
        )
        result = subprocess.run(
            install_cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=int(task.get("timeout") or 600), check=False,
        )

        if result.returncode == 0:
            self._report_task_result(
                result_id,
                "success",
                "install_success",
                progress=100,
                download_progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout or "install_success"),
                stderr_log=self._truncate_log(result.stderr),
            )
        else:
            self._report_task_result(
                result_id, "failed",
                f"exit_code={result.returncode} stderr={result.stderr[:500]}",
                progress=100,
                download_progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout),
                stderr_log=self._truncate_log(result.stderr),
                error_message=f"安装命令退出码={result.returncode}",
            )

    def _do_uninstall(self, result_id, task_id, package_id: int, task: dict, package_info: dict):
        uninstall_cmd = package_info.get("uninstall_command") or task.get("uninstall_command")
        if not uninstall_cmd:
            self._report_task_result(result_id, "failed", f"no_uninstall_command package_id={package_id}")
            return

        print(f"[SoftwareManager] 执行卸载: {uninstall_cmd}")
        self._report_task_result(
            result_id,
            "installing",
            f"开始执行卸载命令 task_id={task_id}",
            progress=10,
            install_progress=10,
        )
        result = subprocess.run(
            uninstall_cmd, shell=True, capture_output=True, text=True,
            errors="replace", timeout=int(task.get("timeout") or 300), check=False,
        )

        if result.returncode == 0:
            self._report_task_result(
                result_id,
                "success",
                "uninstall_success",
                progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout or "uninstall_success"),
                stderr_log=self._truncate_log(result.stderr),
            )
        else:
            self._report_task_result(
                result_id, "failed",
                f"exit_code={result.returncode} stderr={result.stderr[:500]}",
                progress=100,
                install_progress=100,
                stdout_log=self._truncate_log(result.stdout),
                stderr_log=self._truncate_log(result.stderr),
                error_message=f"卸载命令退出码={result.returncode}",
            )

    def _download_file(self, url: str, file_path: Path, result_id: int | None = None, expected_hash: str = ""):
        headers = _software_headers()
        resume_pos = 0
        if file_path.exists():
            resume_pos = file_path.stat().st_size
            headers["Range"] = f"bytes={resume_pos}-"

        mode = "ab" if resume_pos > 0 else "wb"
        with requests.get(url, headers=headers, stream=True, timeout=120, verify=_agent_requests_verify()) as resp:
            if resp.status_code == 416 and file_path.exists():
                # 本地断点文件长度异常时删除后重新下载，避免无限 416。
                file_path.unlink()
                return self._download_file(url, file_path, result_id, expected_hash)
            resp.raise_for_status()
            if resume_pos > 0 and resp.status_code == 200:
                # 服务端未按 Range 返回 206 时必须重写文件，否则会追加出损坏包。
                resume_pos = 0
                mode = "wb"
            total = int(resp.headers.get("Content-Length", 0)) + resume_pos
            downloaded = resume_pos
            expected_hash = expected_hash or str(resp.headers.get("X-File-Hash") or "").strip()
            last_reported_progress = -1
            with open(file_path, mode) as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0 and result_id:
                            progress = int((downloaded / total) * 100)
                            if progress >= 100 or progress - last_reported_progress >= 10:
                                self._report_task_result(
                                    result_id,
                                    "downloading",
                                    f"下载进度 {progress}%",
                                    progress=min(progress, 80),
                                    download_progress=min(progress, 100),
                                )
                                last_reported_progress = progress
                            print(f"[SoftwareManager] 下载进度: {progress}%")

        # SHA256 校验
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()
        if expected_hash and actual_hash.lower() != expected_hash.lower():
            raise RuntimeError(f"软件包 SHA256 校验失败 expected={expected_hash} actual={actual_hash}")
        print(f"[SoftwareManager] 下载完成 SHA256={actual_hash}")

    @staticmethod
    def _safe_file_name(file_name: str) -> str:
        # 只保留文件名，防止服务端数据异常导致写到下载目录之外。
        cleaned = Path(str(file_name)).name.strip()
        return cleaned or "package.bin"

    @staticmethod
    def _build_package_command(command: str | None, file_path: Path) -> str:
        # 安装命令支持显式占位符；没有命令时直接执行下载的软件包。
        quoted_path = f'"{file_path}"'
        if not command:
            return quoted_path
        install_command = str(command)
        for placeholder in ("{file_path}", "{package_path}", "%FILE_PATH%", "%PACKAGE_PATH%"):
            install_command = install_command.replace(f'"{placeholder}"', quoted_path)
            install_command = install_command.replace(f"'{placeholder}'", quoted_path)
            install_command = install_command.replace(placeholder, quoted_path)
        return install_command

    @staticmethod
    def _truncate_log(text: str | None, limit: int = 20000) -> str:
        # 限制单次回传日志长度，避免数据库字段和接口请求过大。
        if not text:
            return ""
        value = str(text)
        if len(value) <= limit:
            return value
        return value[-limit:]

    def _report_task_result(
        self,
        result_id: int | None,
        status: str,
        message: str,
        progress: int | None = None,
        download_progress: int | None = None,
        install_progress: int | None = None,
        stdout_log: str | None = None,
        stderr_log: str | None = None,
        error_message: str | None = None,
    ):
        if result_id is None:
            return
        try:
            if status == "completed":
                status = "success"
            url = urljoin(SOFTWARE_CONFIG["server_url"], f"/api/v1/software/agent/task-results/{result_id}")
            payload = {
                "asset_id": self.asset_id,
                "status": status,
                "completed_at": datetime.now().isoformat(),
            }
            if progress is not None:
                payload["progress"] = max(0, min(100, int(progress)))
            if download_progress is not None:
                payload["download_progress"] = max(0, min(100, int(download_progress)))
            if install_progress is not None:
                payload["install_progress"] = max(0, min(100, int(install_progress)))
            if status in {"failed", "timeout"}:
                payload["error_message"] = self._truncate_log(error_message or message, 1000)
            else:
                payload["stdout_log"] = self._truncate_log(stdout_log or message)
            if stdout_log is not None and "stdout_log" not in payload:
                payload["stdout_log"] = self._truncate_log(stdout_log)
            if stderr_log is not None:
                payload["stderr_log"] = self._truncate_log(stderr_log)

            resp = requests.put(url, json=payload, headers=_software_headers(), timeout=30, verify=_agent_requests_verify())
            if resp.status_code >= 400:
                print(f"[SoftwareManager] 结果上报 HTTP {resp.status_code}: {resp.text[:500]}")
        except Exception as exc:
            print(f"[SoftwareManager] 结果上报失败: {exc}")


# =============================================================================
# 远程桌面服务端


_software_manager_instance: SoftwareManager | None = None


def start_software_management(asset_id: int):
    """启动软件管理（策略同步 + 任务轮询）。"""
    global _software_manager_instance
    if _software_manager_instance is not None:
        return
    _software_manager_instance = SoftwareManager(asset_id)
    _software_manager_instance.start()



