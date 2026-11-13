#!/usr/bin/python3
"""Manual check on a real laptop. Not part of the automated test suite.

    ./tests/hardware/check_real_hardware.py --yes [--stop-service]

Your keyboard will change color and brightness for ~15 seconds; its original
state is restored at the end. Uses a temporary config (yours is not touched)
and does not create the GNOME shortcut. Paste the report it prints into a
"Compatibility report" issue.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from keyboard_rgb import __version__, config  # noqa: E402
from keyboard_rgb.daemon import Daemon  # noqa: E402
from keyboard_rgb.hardware import ClevoSysfsBackend  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{' — ' + detail if detail else ''}")
    return ok


def read(path: str) -> str:
    with open(path) as fh:
        return fh.read().strip()


def dmi(field: str) -> str:
    try:
        return read(f"/sys/class/dmi/id/{field}")
    except OSError:
        return "?"


def service_active() -> bool:
    return shutil.which("systemctl") is not None and subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", "avell-kbd.service"]).returncode == 0


def run_ticks(d: Daemon, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(d.tick() / 1000)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="I understand the keyboard will change colors")
    ap.add_argument("--stop-service", action="store_true",
                    help="stop the avell-kbd user service during the check and restart it afterwards")
    args = ap.parse_args()
    if not args.yes:
        ap.print_help()
        return 2

    restart = False
    if service_active():
        if not args.stop_service:
            print("The avell-kbd service is running and would fight this check. "
                  "Re-run with --stop-service (it is restarted afterwards).")
            return 2
        subprocess.run(["systemctl", "--user", "stop", "avell-kbd.service"], check=True)
        restart = True

    tmp = tempfile.mkdtemp(prefix="kbdrgb-hwcheck-")
    os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")
    os.environ["XDG_RUNTIME_DIR"] = os.path.join(tmp, "run")
    os.makedirs(os.environ["XDG_RUNTIME_DIR"], mode=0o700)

    kb = ClevoSysfsBackend()
    saved: dict[str, str] = {}
    try:
        print(f"Keyboard RGB {__version__} hardware check")
        if not check("keyboard detected", kb.available,
                     f"{kb.kind}, {kb.nzones} zone(s), max brightness {kb.max_brightness}"):
            return 1
        for z in kb.zones:
            for attr in ("brightness", "multi_intensity"):
                p = os.path.join(z, attr)
                if os.path.exists(p):
                    saved[p] = read(p)
        if not check("writable without root", kb.writable()):
            return 1

        z0 = kb.zones[0]
        kb.set_brightness(50)
        check("brightness write/readback", read(os.path.join(z0, "brightness")) == str(kb.brightness_raw(50)),
              read(os.path.join(z0, "brightness")))
        if kb.supports_color:
            for rgb in ([255, 0, 0], [0, 255, 0], [0, 0, 255]):
                kb.set_colors([rgb] * kb.nzones)
                got = read(os.path.join(z0, "multi_intensity"))
                check(f"color {rgb} readback", got == " ".join(map(str, rgb)), got)
                time.sleep(0.4)
            n, t = 40, time.perf_counter()
            for i in range(n):
                kb.set_colors([[i * 6, 255 - i * 6, 128]] * kb.nzones)
            ms = (time.perf_counter() - t) / (n * kb.nzones) * 1000
            check("color write latency", ms < 20, f"{ms:.2f} ms per zone write")

        d = Daemon(backend=kb, tray=False, shortcut=False)
        d.change(on=True, effect="static", brightness=100, zones=[[255, 90, 0]] * 3)
        d.tick()
        ok = read(os.path.join(z0, "brightness")) == str(kb.max_brightness)
        if kb.supports_color:
            ok = ok and read(os.path.join(z0, "multi_intensity")) == "255 90 0"
        check("daemon applies static config", ok)

        if kb.supports_color:
            d.change(effect="breathing", speed=90)
            seen = set()
            c0, w0 = time.process_time(), time.monotonic()
            end = w0 + 5
            while time.monotonic() < end:
                time.sleep(d.tick() / 1000)
                seen.add(read(os.path.join(z0, "multi_intensity")))
            cpu = (time.process_time() - c0) / (time.monotonic() - w0) * 100
            check("breathing effect animates", len(seen) > 20, f"{len(seen)} distinct colors in 5 s")
            check("effect CPU usage", cpu < 10, f"{cpu:.1f}% of one core")

        d.change(on=False)
        d.tick()
        check("off", read(os.path.join(z0, "brightness")) == "0")
        d.change(on=True, effect="static", brightness=100)
        d.tick()
        check("on again", read(os.path.join(z0, "brightness")) == str(kb.max_brightness))

        run_ticks(d, 1.2)
        with open(os.path.join(z0, "brightness"), "w") as fh:  # like an Fn key would
            fh.write(str(kb.brightness_raw(25)))
        run_ticks(d, 1.2)
        check("external brightness change adopted", config.load()["brightness"] == 25,
              f"config brightness {config.load()['brightness']}")
        print("\n  Not checked automatically: the physical Fn brightness keys. Press them now and")
        print("  watch whether /sys/class/leds/*kbd_backlight*/brightness changes, if you can.")
    finally:
        for p, v in saved.items():
            try:
                with open(p, "w") as fh:
                    fh.write(v)
            except OSError as e:
                print(f"!! could not restore {p}: {e}")
        shutil.rmtree(tmp, ignore_errors=True)
        if restart:
            subprocess.run(["systemctl", "--user", "start", "avell-kbd.service"])

    passed = sum(ok for _, ok, _ in results)
    os_name = "?"
    try:
        os_name = platform.freedesktop_os_release().get("PRETTY_NAME", "?")
    except OSError:
        pass
    print("\n----- compatibility report (paste into the issue) -----")
    print(f"Laptop: {dmi('sys_vendor')} {dmi('product_name')} (board {dmi('board_name')})")
    print(f"Distribution: {os_name}")
    print(f"Kernel: {platform.release()}")
    print(f"Session: {os.environ.get('XDG_SESSION_TYPE', '?')} / {os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
    print(f"Keyboard: {kb.kind}, {kb.nzones} zone(s), max brightness {kb.max_brightness}")
    print(f"App version: {__version__}")
    print(f"Result: {passed}/{len(results)} checks passed")
    for name, ok, detail in results:
        print(f"- [{'x' if ok else ' '}] {name}{': ' + detail if detail else ''}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
