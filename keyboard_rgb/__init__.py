"""Keyboard RGB — keyboard backlight control for Clevo-based laptops (e.g. Avell) on Linux.

Package layout:
    config    validated, atomic, locked access to ~/.config/avell-kbd/config.json
    effects   effect definitions and color math (shared by daemon and preview)
    hardware  keyboard backends (currently: Clevo via tuxedo-drivers sysfs)
    status    runtime status the daemon publishes for the settings window
    icons     tray icon / swatch rendering (pure Python, no image files)
    shortcut  GNOME custom keybinding for the global on/off shortcut
    tray      StatusNotifierItem + DBusMenu tray icon
    daemon    background service: applies config, runs effects, owns the tray
    gui       GTK4 / libadwaita settings window
"""

from __future__ import annotations

import os
import shlex
import sys

__version__ = "0.1.0"

APP_ID = "io.github.carlosxjm.KeyboardRGB"
APP_NAME = "Keyboard RGB"
PACKAGE = "my-keyboard-app"
SERVICE = "avell-kbd.service"
PROJECT_URL = "https://github.com/carlosxjm/now-i-can-use-my-keyboard-in-the-dark-while-i-use-linux"

# Set by bin/avell-kbd so the daemon can point the GNOME shortcut and the
# "Settings…" menu item back at the same launcher.
LAUNCHER_ENV = "KEYBOARD_RGB_LAUNCHER"


def launcher_path() -> str | None:
    path = os.environ.get(LAUNCHER_ENV)
    return path if path and os.path.isfile(path) else None


def self_command(*args: str) -> list[str]:
    """argv that runs this app again (via the launcher when known)."""
    launcher = launcher_path()
    if launcher:
        return [launcher, *args]
    return [sys.executable, "-m", "keyboard_rgb", *args]


def self_env() -> dict[str, str]:
    """Environment for self_command() when running without the launcher."""
    env = dict(os.environ)
    if not launcher_path():
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = os.pathsep.join(p for p in (pkg_parent, env.get("PYTHONPATH")) if p)
    return env


def shell_command(*args: str) -> str | None:
    """Shell-quoted launcher command (for the GNOME shortcut), or None if unknown."""
    launcher = launcher_path()
    if not launcher:
        return None
    return " ".join(shlex.quote(a) for a in (launcher, *args))
