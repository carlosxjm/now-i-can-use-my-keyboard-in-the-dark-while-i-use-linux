"""Runtime status published by the daemon ($XDG_RUNTIME_DIR/avell-kbd/status.json).

The settings window reads it to tell the user whether the background service
is running and whether writes to the keyboard are actually succeeding,
instead of assuming that saving the config means the keyboard changed.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from typing import Any


def status_dir() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return os.path.join(runtime, "avell-kbd")
    from .config import config_dir  # no runtime dir (unusual): fall back to the config dir

    return config_dir()


def status_path() -> str:
    return os.path.join(status_dir(), "status.json")


def write(data: dict[str, Any]) -> None:
    os.makedirs(status_dir(), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=status_dir(), prefix=".status.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, status_path())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def clear(pid: int) -> None:
    """Remove the status file if it belongs to `pid` (called on daemon shutdown)."""
    data = read_raw()
    if data and data.get("pid") == pid:
        with contextlib.suppress(OSError):
            os.unlink(status_path())


def read_raw() -> dict[str, Any] | None:
    try:
        with open(status_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _pid_is_daemon(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return b"--daemon" in fh.read()
    except OSError:
        return False


def read() -> dict[str, Any] | None:
    """Status of the running daemon, or None if no daemon is running."""
    data = read_raw()
    return data if data and _pid_is_daemon(data.get("pid")) else None
