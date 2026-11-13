"""Test helpers: isolated environment and a fake sysfs LED tree."""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# Isolate the whole test process from the real desktop *before* anything imports
# GTK/GSettings (which picks its storage backend once per process): settings
# live in memory and XDG dirs point to a throwaway directory, so the suite can
# never modify the user's dconf database (e.g. the real GNOME shortcut).
_SESSION_TMP = tempfile.mkdtemp(prefix="kbdrgb-session-")
atexit.register(shutil.rmtree, _SESSION_TMP, True)
os.environ["GSETTINGS_BACKEND"] = "memory"
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SESSION_TMP, "config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_SESSION_TMP, "cache")
os.environ["XDG_DATA_HOME"] = os.path.join(_SESSION_TMP, "data")


class FakeLeds:
    """Builds /sys/class/leds-like directories under a temp dir."""

    def __init__(self, root: str) -> None:
        self.root = os.path.join(root, "leds")
        self.drivers = os.path.join(root, "drivers")
        os.makedirs(self.root, exist_ok=True)

    def add(self, name: str, *, driver: str | None = "tuxedo_keyboard", rgb: bool = True,
            max_brightness: str = "255", brightness: str = "0",
            multi_index: str = "red green blue") -> str:
        path = os.path.join(self.root, name)
        os.makedirs(path)
        self.write(name, "max_brightness", max_brightness)
        self.write(name, "brightness", brightness)
        if rgb:
            self.write(name, "multi_index", multi_index)
            self.write(name, "multi_intensity", "255 255 255")
        if driver:
            drv = os.path.join(self.drivers, driver)
            dev = os.path.join(self.root, "..", "devices", name)
            os.makedirs(drv, exist_ok=True)
            os.makedirs(dev, exist_ok=True)
            if not os.path.lexists(os.path.join(dev, "driver")):
                os.symlink(drv, os.path.join(dev, "driver"))
            os.symlink(dev, os.path.join(path, "device"))
        return path

    def rgb_zones(self, n: int, **kw) -> list[str]:
        names = ["rgb:kbd_backlight"] + [f"rgb:kbd_backlight_{i}" for i in range(1, n)]
        return [self.add(name, **kw) for name in names]

    def write(self, name: str, attr: str, value: str) -> None:
        with open(os.path.join(self.root, name, attr), "w") as fh:
            fh.write(value + "\n")

    def read(self, name: str, attr: str) -> str:
        with open(os.path.join(self.root, name, attr)) as fh:
            return fh.read().strip()

    def remove(self, name: str) -> None:
        shutil.rmtree(os.path.join(self.root, name))


class IsolatedTestCase(unittest.TestCase):
    """Temp HOME/XDG dirs, fake sysfs and in-memory GSettings for each test."""

    ENV_KEYS = ("HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "KEYBOARD_RGB_SYSFS",
                "AVELL_KBD_SYSFS", "GSETTINGS_BACKEND", "KEYBOARD_RGB_LAUNCHER")

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="kbdrgb-test-")
        self._saved = {k: os.environ.get(k) for k in self.ENV_KEYS}
        for k in ("AVELL_KBD_SYSFS", "KEYBOARD_RGB_LAUNCHER"):
            os.environ.pop(k, None)
        os.environ["HOME"] = os.path.join(self.tmp, "home")
        os.environ["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        os.environ["XDG_RUNTIME_DIR"] = os.path.join(self.tmp, "run")
        os.environ["GSETTINGS_BACKEND"] = "memory"
        os.makedirs(os.environ["HOME"])
        os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)
        self.leds = FakeLeds(self.tmp)
        os.environ["KEYBOARD_RGB_SYSFS"] = self.leds.root

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)


def have_gi() -> bool:
    try:
        import gi  # noqa: F401
        from gi.repository import GLib  # noqa: F401
        return True
    except Exception:
        return False
