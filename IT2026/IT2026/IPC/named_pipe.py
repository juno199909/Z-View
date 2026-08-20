from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable

if os.name == "nt":
    import pywintypes
    import win32file
    import win32pipe
    import win32security
else:  # pragma: norecover - Windows-only runtime
    pywintypes = None
    win32file = None
    win32pipe = None
    win32security = None


def is_named_pipe_available() -> bool:
    return os.name == "nt" and win32pipe is not None and win32file is not None


def build_world_readable_pipe_security_attributes():
    """构造允许交互用户读写的管道 SECURITY_ATTRIBUTES。

    默认 DACL 只允许创建者（SYSTEM 服务）和 Administrators 打开管道，
    普通用户态代理会得到 WinError 5 (拒绝访问)，导致输入委托与
    助手抓帧整体降级。这里显式授予 Everyone 读写。

    安全审计 P0-5：Everyone DACL 意味着同机任意进程（含低权限恶意进程）
    可连接 SYSTEM Helper 管道执行 capture/inject。生产部署应优先使用
    build_session_scoped_pipe_security_attributes（SYSTEM+Admins+会话登录用户），
    并在服务端启用客户端会话校验。本函数保留用于兼容回退。
    """
    if not is_named_pipe_available() or win32security is None:
        return None
    try:
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sd = win32security.SECURITY_DESCRIPTOR()
        everyone = win32security.ConvertStringSidToSid("S-1-1-0")
        acl = win32security.ACL()
        acl.AddAccessAllowedAce(
            win32security.ACL_REVISION,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            everyone,
        )
        sd.SetSecurityDescriptorDacl(True, acl, False)
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False
        return sa
    except Exception:
        return None


def build_session_scoped_pipe_security_attributes(session_id: int, logger=None):
    """构造会话范围的管道 DACL：SYSTEM + Administrators + 该会话登录用户。

    安全审计 P0-5：替代 Everyone 通配，阻断同机其他会话/低权限上下文
    连接 SYSTEM Helper 管道执行 capture/inject 的本地提权辅助路径。
    登录用户 SID 通过 WTSQueryUserToken(session_id) 动态获取。
    构建失败返回 None（调用方回退 world_readable 并记录日志）。
    """
    if not is_named_pipe_available() or win32security is None:
        return None
    try:
        import win32ts

        acl = win32security.ACL()
        sid_specs = [
            ("SYSTEM", "S-1-5-18"),
            ("Administrators", "S-1-5-32-544"),
        ]
        # 会话登录用户（user-session-agent 以该用户身份运行，是主要客户端）
        try:
            user_token = win32ts.WTSQueryUserToken(int(session_id))
            token_user = win32security.GetTokenInformation(
                user_token, win32security.TokenUser
            )
            # pywin32 返回 (PSID, attributes) 元组
            user_sid = (
                token_user[0].Sid if isinstance(token_user, tuple) else token_user.Sid
            )
            sid_specs.append((f"session-{session_id}-user", user_sid))
        except Exception as exc:
            if logger:
                logger(f"session user SID lookup failed for session {session_id}: {exc}")

        for _name, sid_spec in sid_specs:
            sid = (
                win32security.ConvertStringSidToSid(sid_spec)
                if isinstance(sid_spec, str)
                else sid_spec
            )
            acl.AddAccessAllowedAce(
                win32security.ACL_REVISION,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                sid,
            )
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, acl, False)
        sa = pywintypes.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        sa.bInheritHandle = False
        return sa
    except Exception:
        return None


def get_pipe_client_session_id(pipe_handle) -> int | None:
    """获取管道连接的客户端会话 ID（安全审计 P0-5：跨会话连接校验）。"""
    try:
        import win32pipe as _wp

        return int(_wp.GetNamedPipeClientSessionId(pipe_handle))
    except Exception:
        return None


def _pipe_path(pipe_name: str) -> str:
    return fr"\\.\pipe\{pipe_name}"


def read_all_from_pipe(pipe_handle, max_bytes: int = 16 * 1024 * 1024) -> bytes:
    """循环读取直到消息完整（MESSAGE 模式下 ERROR_MORE_DATA 表示还有分片）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        rc, data = win32file.ReadFile(pipe_handle, 262144)
        if data:
            chunks.append(data)
            total += len(data)
        if rc == 0 or not data:
            break
        if rc != 234:  # ERROR_MORE_DATA
            break
        if total > max_bytes:
            break
    return b"".join(chunks)


class NamedPipeCommandServer:
    def __init__(
        self,
        pipe_name: str,
        request_handler: Callable[[dict[str, Any]], dict[str, Any]],
        logger: Callable[[str], None] | None = None,
        allow_all_users: bool = False,
        expected_session_id: int | None = None,
        enforce_session_scope: bool = True,
        client_validator: Callable[[int], bool] | None = None,
    ):
        self.pipe_name = pipe_name
        self.request_handler = request_handler
        self.logger = logger or (lambda message: None)
        # 默认 DACL 仅允许创建者(SYSTEM)与 Administrators 打开管道；
        # 用户态代理需要连接服务/助手管道完成输入委托与抓帧，必须放开。
        self.allow_all_users = bool(allow_all_users)
        # 安全审计 P0-5：会话范围 DACL + 客户端会话校验（阻断跨会话连接）
        self.expected_session_id = expected_session_id
        self.enforce_session_scope = bool(enforce_session_scope)
        # R4：自定义客户端校验（如 ServiceRuntime 用"客户端会话 ∈ 活跃交互会话集合"）
        self.client_validator = client_validator
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not is_named_pipe_available():
            self.logger("named pipe unavailable; IPC server not started")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._serve_forever, name="cmdb-agent-named-pipe", daemon=True)
        self._thread.start()
        self.logger(f"named pipe server started: {_pipe_path(self.pipe_name)}")

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            client = NamedPipeCommandClient(self.pipe_name)
            client.request({"command": "__shutdown__"}, timeout_seconds=1.0)
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=3)
        self.logger("named pipe server stopped")

    def _serve_forever(self) -> None:
        security_attributes = None
        if self.expected_session_id is not None:
            # P0-5：优先会话范围 DACL（SYSTEM+Admins+会话登录用户）
            security_attributes = build_session_scoped_pipe_security_attributes(
                self.expected_session_id, self.logger
            )
            if security_attributes is None:
                self.logger(
                    "session-scoped pipe DACL unavailable; "
                    "falling back to world-readable (P0-5 residual risk)"
                )
                security_attributes = (
                    build_world_readable_pipe_security_attributes()
                    if self.allow_all_users
                    else None
                )
        elif self.allow_all_users:
            security_attributes = build_world_readable_pipe_security_attributes()
        if self.allow_all_users and security_attributes is None:
            self.logger(
                "named pipe security attributes unavailable; "
                "falling back to default DACL (user clients may be denied)"
            )
        while self._running:
            pipe_handle = None
            try:
                pipe_handle = win32pipe.CreateNamedPipe(
                    _pipe_path(self.pipe_name),
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    4 * 1024 * 1024,
                    4 * 1024 * 1024,
                    0,
                    security_attributes,
                )
                win32pipe.ConnectNamedPipe(pipe_handle, None)
                threading.Thread(
                    target=self._handle_client,
                    args=(pipe_handle,),
                    name="cmdb-agent-named-pipe-client",
                    daemon=True,
                ).start()
                pipe_handle = None
            except Exception as exc:
                self.logger(f"named pipe accept failed: {exc}")
                time.sleep(1)
            finally:
                if pipe_handle is not None:
                    try:
                        win32file.CloseHandle(pipe_handle)
                    except Exception:
                        pass

    @staticmethod
    def _read_all(pipe_handle) -> bytes:
        return read_all_from_pipe(pipe_handle)

    def _handle_client(self, pipe_handle) -> None:
        """在单个管道连接上循环处理多轮请求-响应（长连接模式）。"""
        # 安全审计 P0-5：客户端会话校验——仅当客户端会话 ID 可靠获取且明确不匹配时拒绝。
        # pywin32 的 GetNamedPipeClientSessionId 在部分环境返回 0（语义不明），
        # 此时 fail-open 并记录告警，避免误杀合法捕获/注入客户端。
        if self.enforce_session_scope and self.expected_session_id is not None:
            client_sid = get_pipe_client_session_id(pipe_handle)
            if client_sid and int(client_sid) != int(self.expected_session_id):
                self.logger(
                    f"named pipe client rejected: session mismatch "
                    f"client={client_sid} expected={self.expected_session_id}"
                )
                try:
                    win32pipe.DisconnectNamedPipe(pipe_handle)
                    win32file.CloseHandle(pipe_handle)
                except Exception:
                    pass
                return
            if not client_sid:
                self.logger(
                    "named pipe client session unknown (client=0/None); "
                    "fail-open allowed (P0-5 residual risk)"
                )
        # R4：自定义客户端校验（返回 False 即拒绝）
        if self.client_validator is not None:
            client_sid_v = get_pipe_client_session_id(pipe_handle) or 0
            try:
                if not self.client_validator(int(client_sid_v)):
                    self.logger(
                        f"named pipe client rejected by validator: client_session={client_sid_v}"
                    )
                    try:
                        win32pipe.DisconnectNamedPipe(pipe_handle)
                        win32file.CloseHandle(pipe_handle)
                    except Exception:
                        pass
                    return
            except Exception as exc:
                self.logger(f"named pipe client validator error (fail-open): {exc}")
        try:
            while True:
                raw = self._read_all(pipe_handle).rstrip(b"\r\n")
                if not raw:
                    break  # 客户端已断开
                try:
                    request = json.loads(raw.decode("utf-8"))
                except Exception:
                    break
                if request.get("command") == "__shutdown__":
                    payload = json.dumps(
                        {"ok": True, "payload": {"stopping": True}}, ensure_ascii=False
                    ).encode("utf-8") + b"\n"
                    self._write_payload(pipe_handle, payload)
                    return
                response = self.request_handler(request)
                payload = json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
                # MESSAGE 模式下单次 WriteFile 不能超过出缓冲；分块写出，
                # 客户端按 EOF/ERROR_SUCCESS 组装完整字节流。
                self._write_payload(pipe_handle, payload)
        except Exception as exc:
            try:
                payload = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8") + b"\n"
                self._write_payload(pipe_handle, payload)
            except Exception:
                pass
        finally:
            try:
                win32pipe.DisconnectNamedPipe(pipe_handle)
            except Exception:
                pass
            try:
                win32file.CloseHandle(pipe_handle)
            except Exception:
                pass

    @staticmethod
    def _write_payload(pipe_handle, payload: bytes) -> None:
        # MESSAGE 模式下一次 WriteFile 即一条完整消息；出缓冲已扩至 4MB，
        # 必须整条写出，分块会破坏消息边界导致客户端 JSON 截断。
        win32file.WriteFile(pipe_handle, payload)
        win32file.FlushFileBuffers(pipe_handle)

    def _read_message(self, pipe_handle) -> bytes:
        chunks: list[bytes] = []
        while True:
            _, data = win32file.ReadFile(pipe_handle, 65536)
            if not data:
                break
            chunks.append(data)
            if b"\n" in data:
                break
        return b"".join(chunks).rstrip(b"\r\n")


class NamedPipeCommandClient:
    """带长连接复用的管道客户端：同一实例的多次请求复用已建立的管道。"""

    def __init__(self, pipe_name: str):
        self.pipe_name = pipe_name
        self._handle = None
        self._request_lock = threading.Lock()

    def close(self) -> None:
        with self._request_lock:
            if self._handle is not None:
                try:
                    win32file.CloseHandle(self._handle)
                except Exception:
                    pass
                self._handle = None

    def _connect(self, timeout_seconds: float = 5.0):
        start_time = time.time()
        while True:
            try:
                return win32file.CreateFile(
                    _pipe_path(self.pipe_name),
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
            except pywintypes.error as exc:
                if getattr(exc, "winerror", 0) == 231:  # ERROR_PIPE_BUSY
                    try:
                        win32pipe.WaitNamedPipe(_pipe_path(self.pipe_name), 800)
                    except Exception:
                        pass
                if time.time() - start_time >= timeout_seconds:
                    raise
                time.sleep(0.05)

    def request(self, request: dict[str, Any], timeout_seconds: float = 5.0) -> dict[str, Any]:
        if not is_named_pipe_available():
            raise RuntimeError("named pipe IPC is unavailable on this runtime")

        payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
        deadline = time.time() + max(timeout_seconds, 1.0)
        last_error: Exception | None = None

        for attempt in range(3):
            with self._request_lock:
                handle = None
                try:
                    if self._handle is None:
                        self._handle = self._connect(
                            min(timeout_seconds, max(0.5, deadline - time.time()))
                        )
                    handle = self._handle
                    win32pipe.SetNamedPipeHandleState(
                        handle, win32pipe.PIPE_READMODE_MESSAGE, None, None
                    )
                    win32file.WriteFile(handle, payload)
                    response = read_all_from_pipe(handle)
                    return json.loads(response.decode("utf-8").strip() or "{}")
                except Exception as exc:
                    # 管道失效（服务端重启/断开）：丢弃缓存句柄后重试
                    last_error = exc
                    if self._handle is not None:
                        try:
                            win32file.CloseHandle(self._handle)
                        except Exception:
                            pass
                        self._handle = None
                    if time.time() >= deadline:
                        break
                    time.sleep(0.05)

        raise RuntimeError(f"named pipe request failed: {last_error}")
