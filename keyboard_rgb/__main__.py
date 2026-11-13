"""Command line entry point (installed as `avell-kbd`)."""

from __future__ import annotations

import argparse
import logging
import sys

from . import APP_NAME, __version__


def print_status() -> int:
    from .hardware import default_backend, sysfs_root

    kb = default_backend()
    if not kb.available:
        print(f"No supported keyboard found in {sysfs_root()}")
        print("Is the driver installed? See the Troubleshooting section of the README.")
        return 1
    print(f"Type: {kb.kind}  |  zones: {kb.nzones}  |  max brightness: {kb.max_brightness}")
    for zone in kb.zones:
        bright = kb._read(zone, "brightness")
        color = kb._read(zone, "multi_intensity") or ""
        print(f"  {zone} -> brightness {bright} {color}")
    print("Writable without root:", "yes" if kb.writable() else "NO (install the udev rule)")
    from . import status

    st = status.read()
    print("Background service:", f"running (pid {st['pid']})" if st else "not running")
    if st and st.get("error"):
        print("Last error:", st["error"])
    return 0


def toggle() -> int:
    from . import config

    cfg = config.update(on=not config.load()["on"])
    print("on" if cfg["on"] else "off")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="avell-kbd", description=f"{APP_NAME}: keyboard backlight control.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--daemon", action="store_true",
                      help="run the background service (tray icon, effects, restore on login)")
    mode.add_argument("--toggle", action="store_true", help="turn the keyboard light on/off")
    mode.add_argument("--status", action="store_true", help="show the detected hardware and service state")
    mode.add_argument("--remove-shortcut", action="store_true",
                      help="delete the GNOME on/off shortcut (used by the uninstaller)")
    p.add_argument("--no-tray", action="store_true", help="with --daemon: no tray icon or shortcut")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    quiet = logging.INFO if args.daemon else logging.WARNING
    logging.basicConfig(level=logging.DEBUG if args.verbose else quiet,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.daemon:
        from . import daemon

        daemon.run(tray=not args.no_tray)
        return 0
    if args.toggle:
        return toggle()
    if args.status:
        return print_status()
    if args.remove_shortcut:
        from . import shortcut

        shortcut.remove()
        return 0
    from . import gui

    return gui.run()


if __name__ == "__main__":
    sys.exit(main())
