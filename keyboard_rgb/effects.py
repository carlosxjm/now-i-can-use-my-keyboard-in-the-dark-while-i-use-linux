"""Lighting effects. Pure functions, shared by the daemon and the settings preview."""

from __future__ import annotations

import colorsys
import math
from typing import Mapping, Sequence

RGB = list[int]

EFFECTS: list[tuple[str, str]] = [
    ("static", "Static"),
    ("breathing", "Breathing"),
    ("spectrum", "Color cycle"),
    ("wave", "Rainbow wave"),
    ("flash", "Flash"),
]
EFFECT_IDS = [key for key, _ in EFFECTS]
EFFECT_NAMES = dict(EFFECTS)
ANIMATED = frozenset({"breathing", "spectrum", "wave", "flash"})
# Effects that generate their own colors (the user's zone colors are ignored)
SELF_COLORED = frozenset({"spectrum", "wave"})

PRESETS: list[tuple[str, tuple[int, int, int]]] = [
    ("White", (255, 255, 255)),
    ("Red", (255, 0, 0)),
    ("Orange", (255, 90, 0)),
    ("Yellow", (255, 200, 0)),
    ("Green", (0, 255, 0)),
    ("Cyan", (0, 255, 200)),
    ("Blue", (0, 60, 255)),
    ("Purple", (150, 0, 255)),
    ("Pink", (255, 0, 140)),
]

# Animated effects are recomputed at this rate by the daemon
FRAME_INTERVAL_MS = 50


def period_for(speed: int) -> float:
    """Speed 1..100 -> effect period in seconds (12 s .. 0.6 s)."""
    speed = max(1, min(100, int(speed)))
    return 12.0 * (0.05 ** ((speed - 1) / 99.0))


def hue_rgb(h: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return [int(r * 255), int(g * 255), int(b * 255)]


def scale(rgb: Sequence[int], k: float) -> RGB:
    return [max(0, min(255, int(round(c * k)))) for c in rgb]


def compute_colors(cfg: Mapping, t: float, nzones: int) -> list[RGB]:
    """Return the [r, g, b] color of each of the first `nzones` zones at time t (seconds)."""
    nzones = max(1, nzones)
    eff = cfg["effect"]
    zones = [list(z) for z in cfg["zones"][:nzones]]
    while len(zones) < nzones:
        zones.append(list(zones[-1]))
    phase = (t / period_for(cfg["speed"])) % 1.0
    if eff == "breathing":
        k = (1 - math.cos(2 * math.pi * phase)) / 2
        return [scale(z, 0.03 + 0.97 * k) for z in zones]
    if eff == "spectrum":
        return [hue_rgb(phase) for _ in range(nzones)]
    if eff == "wave":
        return [hue_rgb(phase - i / 6.0) for i in range(nzones)]
    if eff == "flash":
        return zones if phase < 0.5 else [[0, 0, 0] for _ in range(nzones)]
    return zones
