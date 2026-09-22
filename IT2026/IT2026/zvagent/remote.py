# -*- coding: utf-8 -*-
"""1.9.56 专项 2：自 cmdb_agent_core.py 逐字迁入（#16/#10 模块化，逻辑未改）。"""
import asyncio
import datetime
import os
import threading
import time
import traceback
import uuid
from pathlib import Path

from config_utils import get_env
from zvagent.config import CONFIG
from zvagent.state import _AGENT_STATE, _LOCK

from zvagent.control import AgentControlServer, StarletteWebSocketAdapter
from zvagent.policy import apply_agent_firewall_whitelist
from zvagent.policy import _load_cached_agent_policies
from zvagent.software import SoftwareManager

class RemoteDesktopServer:
    def __init__(self, host: str | None = None, port: int = 9000):
        host = host or get_env("ZVIEW_BIND_HOST", "0.0.0.0") or "0.0.0.0"  # P0-2: 可配置监听地址
        self.host = host
        self.port = port
        self.running = False
        self.server = None
        self.thread: threading.Thread | None = None

    @staticmethod
    def _log(message: str, exc: BaseException | None = None) -> None:
        lines = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [RemoteDesktopServer] {message}"]
        if exc is not None:
            lines.append(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            log_dir = Path(os.environ.get("ProgramData", ".")) / "CMDB-Agent" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "remote-desktop-server.log", "a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.serve_blocking, name="rdp-server", daemon=True)
        self.thread.start()
        print(f"[RemoteDesktop] 服务端启动: ws://{self.host}:{self.port}/remote-desktop")

    def serve_blocking(self):
        """Run the websocket server loop in the calling thread until stopped."""
        if self.running:
            return
        self.running = True
        try:
            asyncio.set_event_loop(asyncio.new_event_loop())
            loop = asyncio.get_event_loop()
            loop.run_until_complete(self._serve())
        except Exception as exc:
            self._log("服务线程异常退出", exc)
            print(f"[RemoteDesktop] 服务端错误: {exc}")
        finally:
            self.running = False

    def stop(self):
        self.running = False
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass

    def _run_server(self):
        self.serve_blocking()

    async def _serve(self):
        try:
            import websockets
        except ImportError:
            self._log("websockets 未安装，远程桌面不可用")
            print("[RemoteDesktop] websockets 未安装，远程桌面不可用")
            return

        self._log(f"websockets {getattr(websockets, '__version__', 'unknown')} 导入成功，准备监听 ws://{self.host}:{self.port}")

        try:
            apply_agent_firewall_whitelist(
                CONFIG.get("server_url", ""), logger=lambda m: self._log(m)
            )
        except Exception:
            pass

        async def handler(websocket, path=None):
            self._log(f"客户端连接: {websocket.remote_address}")
            try:
                session = self._create_session(StarletteWebSocketAdapter(websocket))
                await session.start()
            except Exception as exc:
                import traceback
                self._log(f"会话错误: {type(exc).__name__}: {exc}")
                self._log("traceback: " + traceback.format_exc()[-800:])
            finally:
                self._log(f"客户端断开: {websocket.remote_address}")

        try:
            self.server = await websockets.serve(
                handler, self.host, self.port,
                ping_interval=20, ping_timeout=10,
            )
            self._log(f"监听成功 ws://{self.host}:{self.port}，等待连接")
            await self.server.wait_closed()
            self._log("server.wait_closed 返回，服务端正常关闭")
        except Exception as exc:
            self._log("serve 失败", exc)
            print(f"[RemoteDesktop] serve 错误: {exc}")

    def _create_session(self, websocket):
        _load_cached_agent_policies()
        session_id = f"session_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        try:
            # 优先使用 v2 引擎
            from remote_desktop_engine_v2 import RemoteDesktopSession as V2Session
            return V2Session(websocket, session_id)
        except Exception as exc:
            print(f"[RemoteDesktop] v2 引擎加载失败 ({exc})，回退到 v1")
            from remote_desktop_engine import RemoteDesktopSession as V1Session
            return V1Session(websocket, session_id)


# =============================================================================
# 公开接口（入口文件依赖这些符号）

_server_instance: RemoteDesktopServer | None = None


def start_remote_desktop_server(wait: bool = False):
    """启动远程桌面 WebSocket 服务端。

    wait=False 时在后台线程运行并立即返回；
    wait=True 时在调用线程内阻塞运行，直到服务端关闭。
    """
    global _server_instance
    if _server_instance is not None:
        return
    _server_instance = RemoteDesktopServer(port=9000)  # host 经 ZVIEW_BIND_HOST 可配置
    if wait:
        _server_instance.serve_blocking()
    else:
        _server_instance.start()

