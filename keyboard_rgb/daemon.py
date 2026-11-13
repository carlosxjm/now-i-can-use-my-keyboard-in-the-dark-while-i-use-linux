"""Background service: applies the config to the keyboard, animates effects,
follows external brightness changes, publishes status and owns the tray icon.

Single-threaded GLib main loop; `tick()` is scheduled again after each run with
the delay it returns, so there is no concurrency to synchronize.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from typing import Any, Callable

from . import config, self_command, self_env, status
from .effects import ANIMATED, FRAME_INTERVAL_MS, SELF_COLORED, compute_colors
from .hardware import KeyboardBackend, default_backend

log = logging.getLogger(__name__)

STATIC_INTERVAL_MS = 100  # how often config changes are picked up when nothing animates
OFF_INTERVAL_MS = 200
MISSING_INTERVAL_MS = 500
REDETECT_EVERY_S = 3.0
SYNC_EVERY_S = 1.0  # how often we look for brightness changed by Fn keys / GNOME
SUSPEND_GAP_S = 3.0  # wall-clock jump that means "we were suspended"


class Daemon:
    def __init__(self, backend: KeyboardBackend | None = None, tray: bool = True,
                 shortcut: bool = True, monotonic: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        self.kb = backend or default_backend()
        self._monotonic, self._wall = monotonic, wall
        config.ensure_exists()
        self.cfg = config.load()
        self.mtime = config.mtime()
        self.t0 = monotonic()
        self.last_wall = wall()
        self.last_detect = self.last_sync = float("-inf")
        self.loop = None
        self.tray = None
        self.shortcut = None
        self._status: dict[str, Any] | None = None
        if shortcut:
            self._init_shortcut()
        if tray:
            self._init_tray()
        self.publish_status()

    def _init_shortcut(self) -> None:
        from .shortcut import Shortcut

        try:
            self.shortcut = Shortcut(self.cfg["shortcut_installed"],
                                     lambda: self.change(shortcut_installed=True),
                                     self._refresh_tray)
        except Exception as e:  # not GNOME, no gi, ...: everything else still works
            log.info("global shortcut unavailable: %s", e)

    def _init_tray(self) -> None:
        from .tray import Tray

        try:
            self.tray = Tray(self)
        except Exception as e:  # no session bus (e.g. started outside the desktop)
            log.warning("tray icon unavailable: %s", e)

    # ------------------------------------------------------------- state for the tray
    @property
    def effective_effect(self) -> str:
        eff = self.cfg["effect"]
        return eff if eff in self.kb.supported_effects else "static"

    def tray_state(self) -> dict[str, Any]:
        return {
            "available": self.kb.available,
            "color_ok": self.kb.supports_color or not self.kb.available,
            "error": self.kb.last_error,
            "shortcut_label": self.shortcut.label() if self.shortcut else None,
        }

    def _refresh_tray(self) -> None:
        if self.tray:
            self.tray.update()

    def publish_status(self) -> None:
        data = {"pid": os.getpid(), **self.kb.describe()}
        if data != self._status:
            self._status = data
            try:
                status.write(data)
            except OSError as e:
                log.warning("cannot write status file: %s", e)
            self._refresh_tray()

    # ------------------------------------------------------------- actions (tray menu)
    def change(self, **kw: Any) -> None:
        self._adopt(config.update(**kw))

    def set_color(self, rgb: list[int]) -> None:
        kw: dict[str, Any] = {"zones": [list(rgb) for _ in range(config.MAX_ZONES)]}
        if self.cfg["effect"] in SELF_COLORED:
            kw["effect"] = "static"
        self.change(**kw)

    def open_settings(self) -> None:
        try:
            subprocess.Popen(self_command(), env=self_env(), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log.error("cannot open the settings window: %s", e)

    def quit(self) -> None:
        if self.loop:
            self.loop.quit()

    def _adopt(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg
        self.mtime = config.mtime()
        self.kb.invalidate()
        self._refresh_tray()

    # ------------------------------------------------------------- main loop
    def _sync_external_brightness(self) -> None:
        """Brightness changed by someone else (Fn keys, GNOME)? Adopt it into the config."""
        cur, mine = self.kb.read_brightness_raw(), self.kb.last_written_brightness_raw()
        if cur is None or mine is None or cur == mine:
            return
        if cur == 0:
            log.info("keyboard turned off externally")
            self.change(on=False)
        else:
            pct = self.kb.raw_to_pct(cur)
            log.info("brightness changed externally to %d%%", pct)
            self.change(on=True, brightness=pct)

    def tick(self) -> int:
        """Apply the current state once. Returns the delay (ms) until the next tick."""
        now, wall = self._monotonic(), self._wall()
        if wall - self.last_wall > SUSPEND_GAP_S:
            log.info("resumed (or clock jumped); rewriting keyboard state")
            self.kb.invalidate()
        self.last_wall = wall

        if not self.kb.available:
            if now - self.last_detect >= REDETECT_EVERY_S:
                self.last_detect = now
                if self.kb.detect():
                    self.kb.invalidate()
            self.publish_status()
            return MISSING_INTERVAL_MS

        if config.mtime() != self.mtime:  # changed by the settings window or --toggle
            self._adopt(config.load())

        if now - self.last_sync >= SYNC_EVERY_S:
            self.last_sync = now
            self._sync_external_brightness()

        cfg = self.cfg
        if not cfg["on"]:
            self.kb.set_brightness(0)
            delay = OFF_INTERVAL_MS
        else:
            if self.kb.supports_color:
                eff_cfg = dict(cfg, effect=self.effective_effect)
                self.kb.set_colors(compute_colors(eff_cfg, now - self.t0, self.kb.nzones))
            self.kb.set_brightness(cfg["brightness"])
            delay = FRAME_INTERVAL_MS if self.effective_effect in ANIMATED else STATIC_INTERVAL_MS
        self.publish_status()
        return delay

    def run(self) -> None:
        from gi.repository import GLib

        self.loop = GLib.MainLoop()

        def schedule() -> bool:
            try:
                delay = self.tick()
            except Exception:  # keep the service alive; log and retry
                log.exception("unexpected error in the main loop")
                delay = 1000
            GLib.timeout_add(delay, schedule)
            return False

        for sig in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.loop.quit)
        log.info("daemon started; keyboard: %s (%d zone(s)); tray: %s",
                 self.kb.kind or "not found", self.kb.nzones, "yes" if self.tray else "no")
        schedule()
        try:
            self.loop.run()
        finally:
            if self.tray:
                self.tray.close()
            status.clear(os.getpid())
            log.info("daemon stopped")


def run(tray: bool = True) -> None:
    Daemon(tray=tray, shortcut=tray).run()
