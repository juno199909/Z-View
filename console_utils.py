"""
Shared console helpers for Windows background/runtime modules.
"""

from __future__ import annotations

import builtins
import os
import sys
from typing import Any, TextIO


def _open_devnull_text_stream() -> TextIO:
    return open(os.devnull, "w", encoding="utf-8", errors="replace")


def _is_usable_stream(stream: TextIO | None) -> bool:
    if stream is None:
        return False

    try:
        if hasattr(stream, "fileno"):
            fd = stream.fileno()
            if isinstance(fd, int) and fd < 0:
                return False
        if hasattr(stream, "flush"):
            stream.flush()
        return True
    except Exception:
        return False


def enable_utf8_stdio() -> None:
    """Best-effort UTF-8 stdio setup without failing on unsupported streams."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if not _is_usable_stream(stream):
            stream = _open_devnull_text_stream()
            setattr(sys, stream_name, stream)

        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                continue


def _resolve_stream(stream: TextIO | None) -> TextIO | None:
    if stream is not None:
        return stream
    return getattr(sys, "stdout", None) or getattr(sys, "__stdout__", None)


def safe_console_print(
    *args: Any,
    sep: str = " ",
    end: str = "\n",
    file: TextIO | None = None,
    flush: bool = False,
) -> None:
    """Print without letting console encoding issues break runtime code paths."""
    stream = _resolve_stream(file)
    if stream is None:
        return

    try:
        builtins.print(*args, sep=sep, end=end, file=stream, flush=flush)
        return
    except UnicodeEncodeError:
        pass
    except Exception:
        try:
            builtins.print(*args, sep=sep, end=end, file=stream)
            if flush:
                stream.flush()
            return
        except Exception:
            pass

    message = sep.join(str(arg) for arg in args)
    output = f"{message}{end}"
    encoding = getattr(stream, "encoding", None) or "utf-8"

    try:
        stream.write(output.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    except Exception:
        fallback = output.encode("ascii", errors="backslashreplace").decode("ascii", errors="ignore")
        try:
            stream.write(fallback)
        except Exception:
            return

    try:
        stream.flush()
    except Exception:
        pass
