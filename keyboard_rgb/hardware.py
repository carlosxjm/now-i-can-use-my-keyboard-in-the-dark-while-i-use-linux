"""Keyboard hardware backends.

Only one backend exists today: Clevo keyboards driven by the `tuxedo_keyboard`
kernel module (from tuxedo-drivers), which exposes standard LED class devices:

    /sys/class/leds/rgb:kbd_backlight[_1|_2]/   RGB keyboards (1 or 3 zones)
        brightness       0..max_brightness (write)
        max_brightness   (read)
        multi_index      channel order, e.g. "red green blue" (read)
        multi_intensity  one 0..255 value per channel, in multi_index order (write)
    /sys/class/leds/white:kbd_backlight/        white-only keyboards (brightness only)

Devices from other drivers are ignored on purpose: we have no evidence that
they behave the same way. New hardware should get its own backend class.

For tests and development, point KEYBOARD_RGB_SYSFS at a fake directory tree.
"""

from __future__ import annotations

import errno
import fnmatch
import logging
import os
from typing import Any, Sequence

log = logging.getLogger(__name__)

DEFAULT_SYSFS = "/sys/class/leds"


def sysfs_root() -> str:
    # AVELL_KBD_SYSFS is the name used by earlier versions
    return os.environ.get("KEYBOARD_RGB_SYSFS") or os.environ.get("AVELL_KBD_SYSFS") or DEFAULT_SYSFS


class KeyboardBackend:
    """Interface every backend implements. All methods must be safe to call when
    no hardware is present: they report failure instead of raising."""

    name = "none"
    kind: str | None = None  # "rgb", "white" or None (not available)
    zones: list[str] = []
    max_brightness = 0
    last_error: str | None = None

    @property
    def available(self) -> bool:
        return self.kind is not None

    @property
    def nzones(self) -> int:
        return len(self.zones) if self.kind == "rgb" else (1 if self.kind else 0)

    @property
    def supports_color(self) -> bool:
        return self.kind == "rgb"

    @property
    def supported_effects(self) -> frozenset[str]:
        if self.supports_color:
            return frozenset({"static", "breathing", "spectrum", "wave", "flash"})
        return frozenset({"static"}) if self.available else frozenset()

    def detect(self) -> bool:
        raise NotImplementedError

    def writable(self) -> bool:
        raise NotImplementedError

    def set_brightness(self, pct: int) -> bool:
        raise NotImplementedError

    def set_colors(self, colors: Sequence[Sequence[int]]) -> bool:
        raise NotImplementedError

    def read_brightness_raw(self) -> int | None:
        raise NotImplementedError

    def last_written_brightness_raw(self) -> int | None:
        """Brightness this process last wrote successfully (None if unknown)."""
        return None

    def invalidate(self) -> None:
        """Forget cached writes so the next set_* call rewrites the hardware."""

    # ---- helpers shared by backends
    def brightness_raw(self, pct: int) -> int:
        if pct <= 0 or self.max_brightness <= 0:
            return 0
        return max(1, min(self.max_brightness, round(pct / 100 * self.max_brightness)))

    def raw_to_pct(self, raw: int) -> int:
        if self.max_brightness <= 0:
            return 0
        return max(0, min(100, round(raw * 100 / self.max_brightness)))

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "kind": self.kind,
            "zones": self.nzones,
            "max_brightness": self.max_brightness,
            "writable": self.writable() if self.available else False,
            "error": self.last_error,
        }


class ClevoSysfsBackend(KeyboardBackend):
    """Clevo keyboards through the tuxedo_keyboard LED class devices."""

    name = "clevo-sysfs"
    SUPPORTED_DRIVERS = frozenset({"tuxedo_keyboard"})
    PATTERNS = ("rgb:kbd_backlight*", "white:kbd_backlight*")
    _GONE = {errno.ENOENT, errno.ENODEV, errno.ENXIO}

    def __init__(self, root: str | None = None) -> None:
        self.root = root or sysfs_root()
        self.kind = None
        self.zones = []
        self.max_brightness = 0
        self.channel_order: list[int] = [0, 1, 2]
        self.last_error = None
        self._cache: dict[tuple[str, str], str] = {}
        self._failing: set[tuple[str, str]] = set()
        self.detect()

    # ---------------------------------------------------------------- detection
    @staticmethod
    def _read(path: str, attr: str) -> str | None:
        try:
            with open(os.path.join(path, attr), encoding="ascii", errors="replace") as fh:
                return fh.read().strip()
        except OSError:
            return None

    @staticmethod
    def _driver(path: str) -> str | None:
        link = os.path.join(path, "device", "driver")
        return os.path.basename(os.path.realpath(link)) if os.path.exists(link) else None

    def _candidates(self) -> list[str]:
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return []
        out = []
        for name in names:
            if not any(fnmatch.fnmatch(name, p) for p in self.PATTERNS):
                continue
            path = os.path.join(self.root, name)
            driver = self._driver(path)
            if driver not in self.SUPPORTED_DRIVERS:
                log.debug("ignoring %s (driver %s is not supported)", name, driver)
                continue
            out.append(path)
        return out

    def _parse_channel_order(self, path: str) -> list[int] | None:
        """Map multi_index ("red green blue" in some order) to positions of r, g, b."""
        idx = (self._read(path, "multi_index") or "").split()
        if sorted(idx) != ["blue", "green", "red"]:
            return None
        return [("red", "green", "blue").index(name) for name in idx]

    def detect(self) -> bool:
        self.kind, self.zones, self.max_brightness = None, [], 0
        self._cache = {}
        self._failing = set()
        paths = self._candidates()
        rgb = [p for p in paths if os.path.basename(p).startswith("rgb:")]
        white = [p for p in paths if os.path.basename(p).startswith("white:")]

        if rgb:
            orders = [self._parse_channel_order(p) for p in rgb]
            if all(o is not None for o in orders) and len({tuple(o) for o in orders}) == 1:
                self.kind, self.zones, self.channel_order = "rgb", rgb, orders[0]
            else:
                log.warning("unexpected multi_index on %s; color control disabled", rgb[0])
                self.kind, self.zones = "white", rgb[:1]
        elif white:
            self.kind, self.zones = "white", white[:1]

        if self.zones:
            raw = self._read(self.zones[0], "max_brightness")
            try:
                self.max_brightness = int(raw or "")
            except ValueError:
                self.max_brightness = 0
            if self.max_brightness <= 0:
                log.warning("invalid max_brightness %r on %s; ignoring the device", raw, self.zones[0])
                self.kind, self.zones = None, []

        if self.kind:
            self.last_error = None
            log.info("keyboard: %s, %d zone(s), max brightness %d",
                     self.kind, self.nzones, self.max_brightness)
        return self.available

    # ---------------------------------------------------------------- I/O
    def writable(self) -> bool:
        if not self.zones:
            return False
        attrs = ["brightness"] + (["multi_intensity"] if self.supports_color else [])
        return all(os.access(os.path.join(z, a), os.W_OK) for z in self.zones for a in attrs)

    def invalidate(self) -> None:
        self._cache = {}

    def _write(self, path: str, attr: str, value: str) -> bool:
        key = (path, attr)
        if self._cache.get(key) == value:
            return True
        try:
            with open(os.path.join(path, attr), "w", encoding="ascii") as fh:
                fh.write(value)
        except OSError as e:
            msg = f"cannot write {os.path.basename(path)}/{attr}: {e.strerror or e}"
            if msg != self.last_error:
                log.error("%s", msg)
            self.last_error = msg
            self._failing.add(key)
            self._cache.pop(key, None)
            if e.errno in self._GONE:
                log.warning("keyboard device disappeared; will look for it again")
                self.kind, self.zones = None, []
            return False
        self._cache[key] = value
        self._failing.discard(key)
        if self.last_error and not self._failing:
            log.info("keyboard writes are working again")
            self.last_error = None
        return True

    def set_brightness(self, pct: int) -> bool:
        if not self.available:
            return False
        raw = str(self.brightness_raw(pct))
        return all([self._write(z, "brightness", raw) for z in list(self.zones)])

    def set_colors(self, colors: Sequence[Sequence[int]]) -> bool:
        if not self.supports_color:
            return False
        ok = True
        for zone, rgb in zip(list(self.zones), colors):
            values = [0, 0, 0]
            for pos, channel in enumerate(self.channel_order):
                values[pos] = max(0, min(255, int(rgb[channel])))
            ok = self._write(zone, "multi_intensity", " ".join(map(str, values))) and ok
        return ok

    def read_brightness_raw(self) -> int | None:
        if not self.zones:
            return None
        raw = self._read(self.zones[0], "brightness")
        try:
            return int(raw or "")
        except ValueError:
            return None

    def last_written_brightness_raw(self) -> int | None:
        if not self.zones:
            return None
        v = self._cache.get((self.zones[0], "brightness"))
        return int(v) if v is not None else None


def default_backend() -> KeyboardBackend:
    return ClevoSysfsBackend()
