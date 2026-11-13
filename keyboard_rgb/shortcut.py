"""Global on/off shortcut, implemented as a GNOME custom keybinding.

Apps can't grab global keys on GNOME, so we register a regular custom shortcut
("Keyboard RGB: toggle light", Ctrl+Super+K) that runs `avell-kbd --toggle`.
It is created once; if the user deletes or changes it in GNOME Settings we
respect that (the config remembers it was installed).
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from . import shell_command

log = logging.getLogger(__name__)

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
ENTRY_SCHEMA = SCHEMA + ".custom-keybinding"
PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/my-keyboard-app/"
DEFAULT_BINDING = "<Control><Super>k"
NAME = "Keyboard RGB: toggle light"

_MODS = {"control": "Ctrl", "primary": "Ctrl", "ctrl": "Ctrl", "super": "Super",
         "alt": "Alt", "shift": "Shift", "meta": "Meta", "hyper": "Hyper"}


def pretty(accel: str | None) -> str | None:
    """'<Control><Super>k' -> 'Ctrl+Super+K' (None if there is no key)."""
    mods = re.findall(r"<([^>]+)>", accel or "")
    key = re.sub(r"<[^>]+>", "", accel or "")
    if not key:
        return None
    key = key.upper() if len(key) == 1 else key.replace("_", " ").title()
    return "+".join([_MODS.get(m.lower(), m.title()) for m in mods] + [key])


def _settings():
    """(parent, entry) Gio.Settings, or None when GNOME's schema isn't installed."""
    from gi.repository import Gio

    src = Gio.SettingsSchemaSource.get_default()
    if not src or not src.lookup(SCHEMA, True):
        return None
    return Gio.Settings.new(SCHEMA), Gio.Settings.new_with_path(ENTRY_SCHEMA, PATH)


def current_label() -> str | None:
    """Binding of our shortcut as text, or None if it doesn't exist (read-only)."""
    try:
        s = _settings()
    except Exception:  # gi missing or broken: no shortcut info
        return None
    if not s:
        return None
    parent, entry = s
    if PATH not in parent.get_strv("custom-keybindings"):
        return None
    return pretty(entry.get_string("binding"))


class Shortcut:
    """Creates/maintains the shortcut and reports changes (daemon side)."""

    def __init__(self, installed_before: bool, mark_installed: Callable[[], None],
                 on_change: Callable[[], None]) -> None:
        from gi.repository import Gio

        s = _settings()
        if not s:
            raise RuntimeError("GNOME media-keys settings are not available")
        self.parent, self.entry = s
        command = shell_command("--toggle")
        paths = list(self.parent.get_strv("custom-keybindings"))
        if PATH not in paths and not installed_before:
            if command:
                self.entry.set_string("name", NAME)
                self.entry.set_string("command", command)
                self.entry.set_string("binding", DEFAULT_BINDING)
                self.parent.set_strv("custom-keybindings", paths + [PATH])
                mark_installed()
                log.info("created GNOME shortcut %s", pretty(DEFAULT_BINDING))
            else:
                log.info("launcher path unknown; not creating the GNOME shortcut")
        elif PATH in paths and command and self.entry.get_string("command") != command:
            self.entry.set_string("command", command)  # app moved (e.g. manual install -> .deb)
            log.info("updated GNOME shortcut command to %s", command)
        Gio.Settings.sync()
        self.label()  # read once so GSettings starts emitting change notifications
        self.parent.connect("changed::custom-keybindings", lambda *_: on_change())
        self.entry.connect("changed::binding", lambda *_: on_change())

    def label(self) -> str | None:
        if PATH not in self.parent.get_strv("custom-keybindings"):
            return None
        return pretty(self.entry.get_string("binding"))


def remove() -> None:
    """Delete our GNOME shortcut (used by the uninstall script)."""
    from gi.repository import Gio

    s = _settings()
    if not s:
        return
    parent, entry = s
    parent.set_strv("custom-keybindings",
                    [p for p in parent.get_strv("custom-keybindings") if p != PATH])
    for key in ("name", "command", "binding"):
        entry.reset(key)
    Gio.Settings.sync()
