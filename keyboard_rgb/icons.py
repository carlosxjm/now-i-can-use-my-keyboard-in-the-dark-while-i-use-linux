"""Tray icon and menu swatch rendering, in pure Python (no image files, no GTK)."""

from __future__ import annotations

import functools
import math
import struct
import zlib
from typing import Callable, Mapping

from .effects import SELF_COLORED, hue_rgb

Color = tuple[int, int, int]
OFF_COLOR: Color = (150, 150, 150)

# Icon keys on a 22x22 grid: (x0, y0, x1, y1).
# Pixel-aligned at 22 px and 44 px so they stay crisp in the top bar.
_ICON_KEYS = (
    [(2 + i * 4, 4, 5 + i * 4, 7) for i in range(5)]
    + [(2 + i * 4, 9, 5 + i * 4, 12) for i in range(5)]
    + [(2, 14, 5, 17), (6, 14, 17, 17), (18, 14, 21, 17)]
)
ICON_SIZES = (16, 22, 32, 44)


def icon_key(cfg: Mapping, available: bool = True) -> tuple:
    """Everything the icon's appearance depends on (used as a cache key)."""
    if not available or not cfg["on"]:
        return ("off",)
    if cfg["effect"] in SELF_COLORED:
        return ("rainbow",)
    zones = tuple(tuple(z) for z in cfg["zones"])
    return ("zones",) + (zones if len(set(zones)) > 1 else zones[:1])


def _color_fn(key: tuple) -> Callable[[float], Color]:
    if key[0] == "off":
        return lambda x: OFF_COLOR
    if key[0] == "rainbow":
        return lambda x: tuple(hue_rgb(x * 0.85))  # type: ignore[return-value]
    zones = key[1:]
    if len(zones) == 1:
        return lambda x: zones[0]
    return lambda x: zones[min(len(zones) - 1, int(x * len(zones)))]


@functools.lru_cache(maxsize=64)
def keyboard_icon_rgba(size: int, key: tuple) -> bytes:
    """RGBA pixels of a keyboard icon of `size` px for the state `key` (see icon_key)."""
    color_at = _color_fn(key)
    ss = 3  # supersampling for smooth edges
    k = 22.0 / size
    r = 0.7  # key corner radius
    out = bytearray(size * size * 4)
    for py in range(size):
        for px in range(size):
            hits = 0
            for sy in range(ss):
                for sx in range(ss):
                    x = (px + (sx + 0.5) / ss) * k
                    y = (py + (sy + 0.5) / ss) * k
                    for (x0, y0, x1, y1) in _ICON_KEYS:
                        if x0 <= x <= x1 and y0 <= y <= y1:
                            dx = max(x0 + r - x, 0, x - (x1 - r))
                            dy = max(y0 + r - y, 0, y - (y1 - r))
                            if dx * dx + dy * dy <= r * r:
                                hits += 1
                            break
            if hits:
                c = color_at((px + 0.5) / size)
                o = (py * size + px) * 4
                out[o:o + 4] = bytes((c[0], c[1], c[2], 255 * hits // (ss * ss)))
    return bytes(out)


def swatch_rgba(size: int, rgb: Color) -> bytes:
    """RGBA pixels of an anti-aliased color dot."""
    out = bytearray(size * size * 4)
    c = (size - 1) / 2
    for py in range(size):
        for px in range(size):
            a = max(0.0, min(1.0, c + 0.5 - math.hypot(px - c, py - c)))
            if a:
                o = (py * size + px) * 4
                out[o:o + 4] = bytes((*rgb, int(255 * a)))
    return bytes(out)


@functools.lru_cache(maxsize=32)
def swatch_png(size: int, rgb: Color) -> bytes:
    return rgba_to_png(size, swatch_rgba(size, rgb))


def rgba_to_png(size: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def rgba_to_argb(rgba: bytes) -> bytes:
    """RGBA -> ARGB32 in network byte order (StatusNotifierItem IconPixmap format)."""
    out = bytearray(len(rgba))
    out[0::4], out[1::4], out[2::4], out[3::4] = rgba[3::4], rgba[0::4], rgba[1::4], rgba[2::4]
    return bytes(out)
