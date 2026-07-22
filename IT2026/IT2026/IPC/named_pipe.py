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
else:  # pragma: no cover - Windows-only runtime
    pywintypes = None
    win32file = None
    win32pipe = None


def is_named_pipe_available() -> bool:
    return os.name == "nt" and win32pipe is not None and win32file is not None


def _pipe_path(pipe_name: str) -> str:
    return fr"\\.\pipe\{pipe_name}"


class NamedPipeCommandServer:
    def __init__(
        self,
        pipe_name: str,
        request_handler: Callable[[dict[str, Any]], dict[str, Any]],
        logger: Callable[[str], None] | None = None,
    ):
        self.pipe_name = pipe_name
        self.request_handler = request_handler
        self.logger = logger or (lambda message: None)
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
        while self._running:
            pipe_handle = None
            try:
                pipe_handle = win32pipe.CreateNamedPipe(
                    _pipe_path(self.pipe_name),
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                    win32pipe.PIPE_UNLIMITED_INSTANCES,
                    65536,
                    65536,
                    0,
                    None,
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

    def _handle_client(self, pipe_handle) -> None:
        try:
            raw = self._read_message(pipe_handle)
            if not raw:
                return
            request = json.loads(raw.decode("utf-8"))
            if request.get("command") == "__shutdown__":
                response = {"ok": True, "payload": {"stopping": True}}
            else:
                response = self.request_handler(request)
            payload = json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n"
            win32file.WriteFile(pipe_handle, payload)
            win32file.FlushFileBuffers(pipe_handle)
        except Exception as exc:
            try:
                payload = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8") + b"\n"
                win32file.WriteFile(pipe_handle, payload)
                win32file.FlushFileBuffers(pipe_handle)
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
    def __init__(self, pipe_name: str):
        self.pipe_name = pipe_name

    def request(self, request: dict[str, Any], timeout_seconds: float = 5.0) -> dict[str, Any]:
        if not is_named_pipe_available():
            raise RuntimeError("named pipe IPC is unavailable on this runtime")

        start_time = time.time()
        last_error: Exception | None = None
        while time.time() - start_time < timeout_seconds:
            pipe_handle = None
            try:
                pipe_handle = win32file.CreateFile(
                    _pipe_path(self.pipe_name),
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
                win32pipe.SetNamedPipeHandleState(pipe_handle, win32pipe.PIPE_READMODE_MESSAGE, None, None)
                payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
                win32file.WriteFile(pipe_handle, payload)
                _, response = win32file.ReadFile(pipe_handle, 65536)
                return json.loads(response.decode("utf-8").strip() or "{}")
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
            finally:
                if pipe_handle is not None:
                    try:
                        win32file.CloseHandle(pipe_handle)
                    except Exception:
                        pass
        raise RuntimeError(f"named pipe request timeout: {last_error}")
