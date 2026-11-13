"""User configuration: ~/.config/avell-kbd/config.json.

The settings window, the daemon, the tray menu and `avell-kbd --toggle` all
write this file, so every change goes through `update()`: it takes an exclusive
lock, re-reads the file, applies only the changed keys and writes atomically.
That way one writer never overwrites another writer's changes with stale data.

The on-disk format is unchanged from earlier versions; values are validated
and clamped on load so a hand-edited or corrupted file can't crash anything.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import logging
import os
import shutil
import tempfile
from typing import Any, Iterator

from .effects import EFFECT_IDS

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "on": True,
    "effect": "static",
    "brightness": 100,  # percent, 0-100
    "speed": 50,  # 1-100
    "zones": [[255, 255, 255], [255, 255, 255], [255, 255, 255]],
    "shortcut_installed": False,  # GNOME shortcut created once; never re-added if the user deletes it
}
MAX_ZONES = 3


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "avell-kbd")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


# --------------------------------------------------------------------------- validation
def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        return default
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, n))


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return default


def _as_rgb(value: Any, default: list[int]) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return list(default)
    return [_as_int(c, d, 0, 255) for c, d in zip(value, default)]


def validate(data: Any) -> dict[str, Any]:
    """Return a complete, valid config built from `data` (unknown keys are dropped)."""
    cfg = defaults()
    if not isinstance(data, dict):
        return cfg
    cfg["on"] = _as_bool(data.get("on"), cfg["on"])
    cfg["effect"] = data.get("effect") if data.get("effect") in EFFECT_IDS else cfg["effect"]
    cfg["brightness"] = _as_int(data.get("brightness"), cfg["brightness"], 0, 100)
    cfg["speed"] = _as_int(data.get("speed"), cfg["speed"], 1, 100)
    cfg["shortcut_installed"] = _as_bool(data.get("shortcut_installed"), cfg["shortcut_installed"])
    raw_zones = data.get("zones")
    zones: list[list[int]] = []
    if isinstance(raw_zones, list):
        for z in raw_zones[:MAX_ZONES]:
            zones.append(_as_rgb(z, zones[-1] if zones else DEFAULTS["zones"][0]))
    while len(zones) < MAX_ZONES:
        zones.append(list(zones[-1]) if zones else list(DEFAULTS["zones"][0]))
    cfg["zones"] = zones
    return cfg


# --------------------------------------------------------------------------- file access
@contextlib.contextmanager
def _locked() -> Iterator[None]:
    os.makedirs(config_dir(), exist_ok=True)
    with open(os.path.join(config_dir(), ".lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load() -> dict[str, Any]:
    """Read and validate the config. Missing or unreadable file -> defaults."""
    path = config_path()
    try:
        with open(path, encoding="utf-8") as fh:
            return validate(json.load(fh))
    except FileNotFoundError:
        return defaults()
    except ValueError as e:
        backup = path + ".corrupt"
        log.warning("config file is not valid JSON (%s); using defaults", e)
        if not os.path.exists(backup):
            with contextlib.suppress(OSError):
                shutil.copy2(path, backup)
                log.warning("kept a copy of the broken file at %s", backup)
        return defaults()
    except OSError as e:
        log.warning("cannot read config file: %s; using defaults", e)
        return defaults()


def _write(cfg: dict[str, Any]) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config_dir(), prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, config_path())
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def save(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate and write a full config. Prefer update() for partial changes."""
    cfg = validate(cfg)
    with _locked():
        _write(cfg)
    return cfg


def update(**changes: Any) -> dict[str, Any]:
    """Atomically apply `changes` on top of the current file and return the new config."""
    with _locked():
        cfg = load()
        cfg.update(changes)
        cfg = validate(cfg)
        _write(cfg)
    return cfg


def ensure_exists() -> None:
    with _locked():
        if not os.path.exists(config_path()):
            _write(defaults())


def mtime() -> int:
    try:
        return os.stat(config_path()).st_mtime_ns
    except OSError:
        return 0
