#!/usr/bin/env python3
"""Keyboard RGB — keyboard backlight control for Clevo-based laptops (e.g. Avell) on Linux.

Usage:
    avell-kbd              open the settings window
    avell-kbd --daemon     background service: tray icon, effects, restore on login
                           (--no-tray to run without the icon)
    avell-kbd --toggle     turn the light on/off (bound to a GNOME shortcut, Ctrl+Super+K by default)
    avell-kbd --status     show the detected hardware

Hardware access goes through the tuxedo-drivers kernel modules (clevo_acpi +
tuxedo_keyboard), which expose the keyboard at /sys/class/leds/*:kbd_backlight*.
The window only writes the config file; the daemon is what drives the keyboard.
"""

import colorsys
import glob
import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

APP_ID = "vet.local.AvellKbd"
SYSFS_LEDS = os.environ.get("AVELL_KBD_SYSFS", "/sys/class/leds")
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "avell-kbd"
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
SERVICE = "avell-kbd.service"

EFFECTS = [
    ("static", "Static"),
    ("breathing", "Breathing"),
    ("spectrum", "Color cycle"),
    ("wave", "Rainbow wave"),
    ("flash", "Flash"),
]
ANIMATED = {"breathing", "spectrum", "wave", "flash"}

PRESETS = [
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

DEFAULT_CONFIG = {
    "on": True,
    "effect": "static",
    "brightness": 100,  # 0-100 %
    "speed": 50,  # 1-100
    "zones": [[255, 255, 255]] * 3,
    "shortcut_installed": False,  # GNOME shortcut created once; never re-added if the user deletes it
}


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except (OSError, ValueError):
        pass
    zones = [list(map(int, z))[:3] for z in cfg.get("zones", [])][:3]
    while len(zones) < 3:
        zones.append(list(zones[-1]) if zones else [255, 255, 255])
    cfg["zones"] = zones
    cfg["brightness"] = max(0, min(100, int(cfg["brightness"])))
    cfg["speed"] = max(1, min(100, int(cfg["speed"])))
    if cfg["effect"] not in dict(EFFECTS):
        cfg["effect"] = "static"
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def config_mtime():
    try:
        return os.stat(CONFIG_PATH).st_mtime_ns
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# Effects (shared by the daemon and the preview in the settings window)
# --------------------------------------------------------------------------- #
def period_for(speed):
    """Speed 1..100 -> effect period in seconds (12s .. 0.6s)."""
    return 12.0 * (0.05 ** ((speed - 1) / 99.0))


def hue_rgb(h):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, 1.0, 1.0)
    return [int(r * 255), int(g * 255), int(b * 255)]


def scale(rgb, k):
    return [max(0, min(255, int(round(c * k)))) for c in rgb]


def compute_colors(cfg, t, nzones):
    """Return the [r,g,b] color of each zone at time t."""
    eff = cfg["effect"]
    zones = cfg["zones"][:nzones]
    phase = (t / period_for(cfg["speed"])) % 1.0
    if eff == "breathing":
        k = (1 - math.cos(2 * math.pi * phase)) / 2
        return [scale(z, 0.03 + 0.97 * k) for z in zones]
    if eff == "spectrum":
        return [hue_rgb(phase)] * nzones
    if eff == "wave":
        if nzones == 1:
            return [hue_rgb(phase)]
        return [hue_rgb(phase - i / 6.0) for i in range(nzones)]
    if eff == "flash":
        return zones if phase < 0.5 else [[0, 0, 0]] * nzones
    return zones


# --------------------------------------------------------------------------- #
# Hardware (sysfs)
# --------------------------------------------------------------------------- #
class Keyboard:
    def __init__(self):
        self.detect()

    def detect(self):
        paths = sorted(glob.glob(os.path.join(SYSFS_LEDS, "*:kbd_backlight*")))
        rgb = [p for p in paths if os.path.exists(os.path.join(p, "multi_intensity"))]
        if rgb:
            self.kind, self.zones = "rgb", rgb
        elif paths:
            self.kind, self.zones = "white", paths[:1]
        else:
            self.kind, self.zones = None, []
        self.max_brightness = int(self._read(self.zones[0], "max_brightness") or 255) if self.zones else 255
        self._cache = {}
        return self.kind is not None

    @property
    def nzones(self):
        return len(self.zones) if self.kind == "rgb" else 1

    @staticmethod
    def _read(path, attr):
        try:
            with open(os.path.join(path, attr)) as f:
                return f.read().strip()
        except OSError:
            return None

    def _write(self, path, attr, value):
        key = (path, attr)
        if self._cache.get(key) == value:
            return True
        try:
            with open(os.path.join(path, attr), "w") as f:
                f.write(value)
            self._cache[key] = value
            return True
        except OSError as e:
            if not getattr(self, "_warned", False):
                print(f"[avell-kbd] failed to write {path}/{attr}: {e}", file=sys.stderr)
                self._warned = True
            return False

    def writable(self):
        return bool(self.zones) and os.access(os.path.join(self.zones[0], "brightness"), os.W_OK)

    def invalidate(self):
        self._cache = {}

    def brightness_raw(self, pct):
        if pct <= 0:
            return 0
        return max(1, round(pct / 100 * self.max_brightness))

    def set_brightness(self, pct):
        raw = str(self.brightness_raw(pct))
        for z in self.zones:
            self._write(z, "brightness", raw)

    def read_brightness_pct(self):
        if not self.zones:
            return None
        raw = self._read(self.zones[0], "brightness")
        if raw is None:
            return None
        return round(int(raw) * 100 / self.max_brightness)

    def last_written_brightness(self):
        v = self._cache.get((self.zones[0], "brightness")) if self.zones else None
        return int(v) if v is not None else None

    def set_colors(self, colors):
        if self.kind != "rgb":
            return
        for z, c in zip(self.zones, colors):
            self._write(z, "multi_intensity", f"{c[0]} {c[1]} {c[2]}")


# --------------------------------------------------------------------------- #
# Icons (drawn in code, no external files)
# --------------------------------------------------------------------------- #
def _icon_colors(cfg):
    """Function x(0..1) -> key color in the icon, based on the current state."""
    if not cfg["on"]:
        return lambda x: (150, 150, 150)
    if cfg["effect"] in ("spectrum", "wave"):
        return lambda x: tuple(hue_rgb(x * 0.85))
    zones = cfg["zones"]
    return lambda x: tuple(zones[min(2, int(x * 3))]) if len(set(map(tuple, zones))) > 1 \
        else tuple(zones[0])


# Icon keys on a 22x22 grid: (x0, y0, x1, y1)
# (pixel-aligned at 22px and 44px so they stay crisp in the top bar)
_ICON_KEYS = (
    [(2 + i * 4, 4, 5 + i * 4, 7) for i in range(5)]
    + [(2 + i * 4, 9, 5 + i * 4, 12) for i in range(5)]
    + [(2, 14, 5, 17), (6, 14, 17, 17), (18, 14, 21, 17)]
)


def draw_keyboard_icon(size, cfg):
    """Return the RGBA pixels (bytes) of a colored keyboard icon."""
    color_at = _icon_colors(cfg)
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


def draw_swatch(size, rgb, selected=False):
    out = bytearray(size * size * 4)
    c = (size - 1) / 2
    for py in range(size):
        for px in range(size):
            d = math.hypot(px - c, py - c)
            a = max(0.0, min(1.0, c + 0.5 - d))
            if a:
                o = (py * size + px) * 4
                out[o:o + 4] = bytes((*rgb, int(255 * a)))
    return bytes(out)


def rgba_to_png(size, rgba):
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def rgba_to_argb(rgba):
    """RGBA -> ARGB32 in network byte order (StatusNotifierItem IconPixmap format)."""
    out = bytearray(len(rgba))
    out[0::4], out[1::4], out[2::4], out[3::4] = rgba[3::4], rgba[0::4], rgba[1::4], rgba[2::4]
    return bytes(out)


# --------------------------------------------------------------------------- #
# Tray icon (StatusNotifierItem + DBusMenu, spoken directly over D-Bus)
# --------------------------------------------------------------------------- #
SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>"""

DBUSMENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>"""

_PROP_TYPES = {"label": "s", "enabled": "b", "visible": "b", "type": "s", "toggle-type": "s",
               "toggle-state": "i", "children-display": "s", "icon-name": "s", "icon-data": "ay"}


class Tray:
    SNI_PATH = "/StatusNotifierItem"
    MENU_PATH = "/MenuBar"

    def __init__(self, daemon):
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.daemon = daemon
        self.revision = 1
        self.items = {}
        self.root = None
        self._pixmaps = []
        self._swatches = {}
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_XML).interfaces[0]
        self.conn.register_object(self.SNI_PATH, sni, self._sni_call, self._sni_prop, None)
        self.conn.register_object(self.MENU_PATH, menu, self._menu_call, self._menu_prop, None)
        self.update(daemon.cfg, emit=False)
        Gio.bus_own_name_on_connection(self.conn, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        # Register with the StatusNotifierWatcher (and again if GNOME Shell restarts)
        Gio.bus_watch_name_on_connection(self.conn, "org.kde.StatusNotifierWatcher",
                                         Gio.BusNameWatcherFlags.NONE, self._register, None)

    def _register(self, conn, name, owner):
        conn.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                  "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                  self.GLib.Variant("(s)", (self.name,)), None, self.Gio.DBusCallFlags.NONE,
                  -1, None, None)

    # ---------------- state -> icon / menu
    def update(self, cfg, emit=True):
        self._pixmaps = [(s, s, rgba_to_argb(draw_keyboard_icon(s, cfg))) for s in (16, 22, 32, 44)]
        self.root = self._build_menu(cfg)
        self.revision += 1
        if emit:
            for sig in ("NewIcon", "NewToolTip"):
                self.conn.emit_signal(None, self.SNI_PATH, "org.kde.StatusNotifierItem", sig, None)
            self.conn.emit_signal(None, self.MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                                  self.GLib.Variant("(ui)", (self.revision, 0)))

    def _swatch_png(self, rgb):
        key = tuple(rgb)
        if key not in self._swatches:
            self._swatches[key] = rgba_to_png(16, draw_swatch(16, key))
        return self._swatches[key]

    def _build_menu(self, cfg):
        d = self.daemon
        self.items = {}
        counter = [0]

        def node(label=None, action=None, children=None, **props):
            counter[0] += 1
            n = {"id": counter[0], "props": dict(props), "action": action, "children": children or []}
            if label is not None:
                n["props"]["label"] = label
            if children:
                n["props"]["children-display"] = "submenu"
            self.items[n["id"]] = n
            return n

        def sep():
            return node(type="separator")

        def radio(label, active, action, **props):
            return node(label, action, **{"toggle-type": "radio", "toggle-state": int(active)}, **props)

        rgb = d.kb.kind in ("rgb", None)
        cur = cfg["zones"][0]
        effects = [radio(name, cfg["effect"] == key, lambda k=key: d.change(effect=k))
                   for key, name in EFFECTS]
        colors = [radio(name, cfg["effect"] not in ("spectrum", "wave") and list(c) == cur,
                        lambda c=c: d.set_color(c), **{"icon-data": self._swatch_png(c)})
                  for name, c in PRESETS]
        levels = [radio(f"{v}%", cfg["brightness"] == v, lambda v=v: d.change(brightness=v, on=True))
                  for v in (10, 25, 50, 75, 100)]

        root = node(children=[
            node(d.on_label(), lambda: d.change(on=not cfg["on"]),
                 **{"toggle-type": "checkmark", "toggle-state": int(cfg["on"])}),
            sep(),
            node("Effect", children=effects, enabled=rgb),
            node("Color", children=colors, enabled=rgb),
            node("Brightness", children=levels),
            sep(),
            node("Settings…", d.open_settings),
            node("Quit", d.quit),
        ])
        root["id"] = 0
        self.items = {(0 if v is root else k): v for k, v in self.items.items()}
        return root

    def _props_variant(self, n, names=None):
        V = self.GLib.Variant
        return {k: V(_PROP_TYPES[k], v) for k, v in n["props"].items()
                if (not names or k in names) and k in _PROP_TYPES}

    def _layout(self, n, depth, names):
        V = self.GLib.Variant
        kids = []
        if depth != 0:
            kids = [V("(ia{sv}av)", self._layout(c, depth - 1, names)) for c in n["children"]]
        return (n["id"], self._props_variant(n, names), kids)

    # ---------------- D-Bus: StatusNotifierItem
    def _sni_prop(self, conn, sender, path, iface, prop):
        V = self.GLib.Variant
        cfg = self.daemon.cfg
        status = f"{dict(EFFECTS)[cfg['effect']]} · {cfg['brightness']}%" if cfg["on"] else "Off"
        return {
            "Category": V("s", "Hardware"),
            "Id": V("s", "my-keyboard-app"),
            "Title": V("s", "Keyboard RGB"),
            "Status": V("s", "Active"),
            "IconName": V("s", ""),
            "IconPixmap": V("a(iiay)", self._pixmaps),
            "IconThemePath": V("s", ""),
            "ToolTip": V("(sa(iiay)ss)", ("", [], "Keyboard RGB", status)),
            "ItemIsMenu": V("b", True),
            "Menu": V("o", self.MENU_PATH),
        }.get(prop)

    def _sni_call(self, conn, sender, path, iface, method, params, inv):
        if method == "SecondaryActivate":  # middle click: toggle on/off
            self.daemon.change(on=not self.daemon.cfg["on"])
        elif method == "Scroll":  # scroll: brightness
            delta, orientation = params.unpack()
            if delta and orientation == "vertical":
                b = self.daemon.cfg["brightness"] + (-5 if delta > 0 else 5)
                self.daemon.change(brightness=max(5, min(100, b)), on=True)
        inv.return_value(None)

    # ---------------- D-Bus: DBusMenu
    def _menu_prop(self, conn, sender, path, iface, prop):
        V = self.GLib.Variant
        return {"Version": V("u", 3), "TextDirection": V("s", "ltr"), "Status": V("s", "normal"),
                "IconThemePath": V("as", [])}.get(prop)

    def _menu_call(self, conn, sender, path, iface, method, params, inv):
        V = self.GLib.Variant
        if method == "GetLayout":
            parent, depth, names = params.unpack()
            n = self.items.get(parent, self.root)
            inv.return_value(V("(u(ia{sv}av))", (self.revision, self._layout(n, depth, names))))
        elif method == "GetGroupProperties":
            ids, names = params.unpack()
            res = [(i, self._props_variant(self.items[i], names)) for i in ids if i in self.items]
            inv.return_value(V("(a(ia{sv}))", (res,)))
        elif method == "GetProperty":
            i, name = params.unpack()
            props = self._props_variant(self.items.get(i, self.root))
            inv.return_value(V("(v)", (props.get(name, V("s", "")),)))
        elif method == "Event":
            i, event = params.unpack()[:2]
            self._event(i, event)
            inv.return_value(None)
        elif method == "EventGroup":
            events = params.unpack()[0]
            for i, event, *_ in events:
                self._event(i, event)
            inv.return_value(V("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(V("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(V("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def _event(self, i, event):
        n = self.items.get(i)
        if event == "clicked" and n and n["action"]:
            # run outside the D-Bus handler so the shell gets a quick reply
            self.GLib.idle_add(lambda: (n["action"](), False)[1])


# --------------------------------------------------------------------------- #
# Global on/off shortcut (a GNOME custom keybinding running "avell-kbd --toggle")
# --------------------------------------------------------------------------- #
class Shortcut:
    SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
    PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/my-keyboard-app/"
    DEFAULT = "<Control><Super>k"
    MODS = {"control": "Ctrl", "primary": "Ctrl", "ctrl": "Ctrl", "super": "Super",
            "alt": "Alt", "shift": "Shift", "meta": "Meta", "hyper": "Hyper"}

    def __init__(self, daemon, on_change):
        from gi.repository import Gio

        src = Gio.SettingsSchemaSource.get_default()
        if not src or not src.lookup(self.SCHEMA, True):
            raise RuntimeError("GNOME media-keys settings not available")
        self.parent = Gio.Settings.new(self.SCHEMA)
        self.entry = Gio.Settings.new_with_path(self.SCHEMA + ".custom-keybinding", self.PATH)
        command = f"{shlex.quote(os.path.abspath(__file__))} --toggle"
        paths = list(self.parent.get_strv("custom-keybindings"))
        if self.PATH not in paths and not daemon.cfg.get("shortcut_installed"):
            self.entry.set_string("name", "Keyboard RGB: toggle light")
            self.entry.set_string("command", command)
            self.entry.set_string("binding", self.DEFAULT)
            self.parent.set_strv("custom-keybindings", paths + [self.PATH])
            daemon.cfg["shortcut_installed"] = True
            save_config(daemon.cfg)
        elif self.PATH in paths and self.entry.get_string("command") != command:
            self.entry.set_string("command", command)  # app moved (e.g. manual install -> .deb)
        Gio.Settings.sync()
        # read once so GSettings starts emitting change notifications
        self.label()
        self.parent.connect("changed::custom-keybindings", lambda *_: on_change())
        self.entry.connect("changed::binding", lambda *_: on_change())

    def label(self):
        """Human-readable binding, e.g. "Ctrl+Super+K", or None if there is none."""
        if self.PATH not in self.parent.get_strv("custom-keybindings"):
            return None
        return self.pretty(self.entry.get_string("binding"))

    @classmethod
    def pretty(cls, accel):
        mods = re.findall(r"<([^>]+)>", accel or "")
        key = re.sub(r"<[^>]+>", "", accel or "")
        if not key:
            return None
        key = key.upper() if len(key) == 1 else key.replace("_", " ").title()
        return "+".join([cls.MODS.get(m.lower(), m.title()) for m in mods] + [key])

    @classmethod
    def remove(cls):
        """Delete the GNOME shortcut (used by the uninstall script)."""
        from gi.repository import Gio

        src = Gio.SettingsSchemaSource.get_default()
        if not src or not src.lookup(cls.SCHEMA, True):
            return
        parent = Gio.Settings.new(cls.SCHEMA)
        parent.set_strv("custom-keybindings",
                        [p for p in parent.get_strv("custom-keybindings") if p != cls.PATH])
        entry = Gio.Settings.new_with_path(cls.SCHEMA + ".custom-keybinding", cls.PATH)
        for k in ("name", "command", "binding"):
            entry.reset(k)
        Gio.Settings.sync()


# --------------------------------------------------------------------------- #
# Daemon (drives the keyboard, runs effects and owns the tray icon)
# --------------------------------------------------------------------------- #
class Daemon:
    def __init__(self, tray=True):
        from gi.repository import GLib

        self.GLib = GLib
        self.loop = GLib.MainLoop()
        self.kb = Keyboard()
        if not os.path.exists(CONFIG_PATH):
            save_config(load_config())
        self.cfg, self.mtime = load_config(), -1
        self.last_wall = time.time()
        self.last_detect = self.last_sync = 0.0
        self.t0 = time.monotonic()
        self.tray = None
        self.shortcut = None
        if tray:
            try:
                self.shortcut = Shortcut(self, self._shortcut_changed)
            except Exception as e:
                print(f"[avell-kbd] shortcut unavailable: {e}", file=sys.stderr)
            try:
                self.tray = Tray(self)
            except Exception as e:  # no graphical session / D-Bus: keep applying colors anyway
                print(f"[avell-kbd] tray unavailable: {e}", file=sys.stderr)

    # ---------------- actions (used by the tray menu)
    def on_label(self):
        key = self.shortcut.label() if self.shortcut else None
        return f"On   ({key})" if key else "On"

    def _shortcut_changed(self):
        if self.tray:
            self.tray.update(self.cfg)

    def change(self, **kw):
        self.cfg.update(kw)
        save_config(self.cfg)
        self._reload()

    def set_color(self, rgb):
        kw = {"zones": [list(rgb) for _ in range(3)]}
        if self.cfg["effect"] in ("spectrum", "wave"):
            kw["effect"] = "static"
        self.change(**kw)

    def open_settings(self):
        subprocess.Popen([sys.executable, os.path.abspath(__file__)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def quit(self):
        self.loop.quit()

    def _reload(self):
        self.mtime, self.cfg = config_mtime(), load_config()
        self.kb.invalidate()
        if self.tray:
            self.tray.update(self.cfg)

    # ---------------- main loop
    def tick(self):
        kb, cfg = self.kb, self.cfg
        now, wall = time.monotonic(), time.time()
        # Back from suspend? The firmware may have reset the keyboard: rewrite everything.
        if wall - self.last_wall > 3:
            kb.invalidate()
        self.last_wall = wall

        if not kb.kind:
            if now - self.last_detect > 3:
                self.last_detect = now
                if kb.detect():
                    print(f"[avell-kbd] keyboard detected: {kb.kind} ({kb.nzones} zone(s))")
                    self._reload()
            return 500

        if config_mtime() != self.mtime:  # changed by the settings window
            self._reload()
            cfg = self.cfg

        # Brightness changed elsewhere (Fn keys, GNOME)? Adopt the new value.
        if now - self.last_sync > 1 and cfg["on"]:
            self.last_sync = now
            cur, mine = kb.read_brightness_pct(), kb.last_written_brightness()
            if cur is not None and mine is not None and kb.brightness_raw(cur) != mine:
                if cur == 0:
                    self.change(on=False)
                else:
                    self.change(brightness=cur)
                cfg = self.cfg

        if not cfg["on"]:
            kb.set_brightness(0)
            return 200

        kb.set_colors(compute_colors(cfg, now - self.t0, kb.nzones))
        kb.set_brightness(cfg["brightness"])
        return 50 if cfg["effect"] in ANIMATED else 100

    def _schedule(self):
        try:
            delay = self.tick()
        except Exception as e:
            print(f"[avell-kbd] error: {e}", file=sys.stderr)
            delay = 1000
        self.GLib.timeout_add(delay, self._schedule)
        return False

    def run(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.GLib.unix_signal_add(self.GLib.PRIORITY_DEFAULT, sig, self.loop.quit)
        print(f"[avell-kbd] daemon started; keyboard: {self.kb.kind or 'not found'} "
              f"({self.kb.nzones} zone(s)); tray: {'yes' if self.tray else 'no'}")
        self._schedule()
        self.loop.run()


def run_daemon(tray=True):
    Daemon(tray=tray).run()


def toggle():
    cfg = load_config()
    cfg["on"] = not cfg["on"]
    save_config(cfg)
    print("on" if cfg["on"] else "off")


def print_status():
    kb = Keyboard()
    if not kb.kind:
        print("No backlit keyboard found in", SYSFS_LEDS)
        print("Install the driver with: sudo ./install-driver.sh")
        return 1
    print(f"Type: {kb.kind}  |  zones: {kb.nzones}  |  max brightness: {kb.max_brightness}")
    for z in kb.zones:
        print(" ", z, "->", kb._read(z, "brightness"), kb._read(z, "multi_intensity") or "")
    print("Writable without root:", "yes" if kb.writable() else "NO (install the udev rule)")
    return 0


# --------------------------------------------------------------------------- #
# Settings window (GTK4 + libadwaita)
# --------------------------------------------------------------------------- #
def run_gui():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Gsk", "4.0")
    gi.require_version("Graphene", "1.0")
    from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk

    CSS = """
    .swatch { min-width: 34px; min-height: 34px; padding: 0; border-radius: 999px;
              border: 2px solid alpha(currentColor, 0.15); }
    .swatch.selected { border: 3px solid @accent_color; }
    .preview-frame { border-radius: 16px; background: #15161a; }
    """
    for i, (_, (r, g, b)) in enumerate(PRESETS):
        CSS += f".swatch-{i} {{ background: rgb({r},{g},{b}); }}\n"

    def ensure_daemon():
        if shutil.which("systemctl"):
            r = subprocess.run(["systemctl", "--user", "start", SERVICE],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if r.returncode == 0:
                return
        # Service not installed: start a daemon unless one is already running.
        r = subprocess.run(["pgrep", "-f", "avell_kbd.py --daemon|avell-kbd --daemon"],
                           stdout=subprocess.DEVNULL)
        if r.returncode != 0:
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "--daemon"],
                             start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    class KeyboardPreview(Gtk.Widget):
        ROWS = [15, 15, 14, 13, 12, 9]

        def __init__(self, win):
            super().__init__()
            self.win = win
            self.set_size_request(-1, 190)
            self.set_hexpand(True)
            self.t0 = time.monotonic()
            self.add_tick_callback(self.tick)

        def tick(self, *_):
            if self.win.cfg["effect"] in ANIMATED and self.win.cfg["on"]:
                self.queue_draw()
            return GLib.SOURCE_CONTINUE

        def do_snapshot(self, snap):
            w, h = self.get_width(), self.get_height()
            cfg, nz = self.win.cfg, self.win.nzones
            colors = compute_colors(cfg, time.monotonic() - self.t0, nz)
            bright = cfg["brightness"] / 100 if cfg["on"] else 0
            pad, gap = 16, 5
            kw = min((w - 2 * pad) / 15.0, (h - 2 * pad) / 6.0 * 1.15)
            kh = kw / 1.15
            ox = (w - kw * 15) / 2
            oy = (h - kh * 6) / 2
            for row, n in enumerate(self.ROWS):
                key_w = 15 * kw / n
                for col in range(n):
                    x = ox + col * key_w
                    cx = (x + key_w / 2 - ox) / (15 * kw)
                    zi = min(nz - 1, int(cx * nz))
                    r, g, b = (c / 255 for c in colors[zi])
                    if self.win.kind == "white":
                        r = g = b = 1.0
                    k = 0.12 + 0.88 * bright
                    rect = Graphene.Rect().init(x + gap / 2, oy + row * kh + gap / 2,
                                                key_w - gap, kh - gap)
                    color = Gdk.RGBA()
                    color.red = 0.13 + r * k * 0.87
                    color.green = 0.13 + g * k * 0.87
                    color.blue = 0.14 + b * k * 0.86
                    color.alpha = 1.0
                    rr = Gsk.RoundedRect()  # keep a reference (avoids a use-after-free in PyGObject)
                    rr.init_from_rect(rect, 4)
                    snap.push_rounded_clip(rr)
                    snap.append_color(color, rect)
                    snap.pop()

    class Window(Adw.ApplicationWindow):
        def __init__(self, app):
            super().__init__(application=app, title="Keyboard RGB")
            self.set_default_size(560, 860)
            self.cfg = load_config()
            self.kb = Keyboard()
            self.kind = self.kb.kind or "rgb"  # no driver: RGB demo mode
            self.nzones = self.kb.nzones if self.kb.kind else 3
            self.zone_sel = None  # None = all zones
            self._updating = False

            view = Adw.ToolbarView()
            header = Adw.HeaderBar()
            self.power = Gtk.Switch(valign=Gtk.Align.CENTER, active=self.cfg["on"],
                                    tooltip_text="Turn the keyboard light on/off")
            self.power.connect("notify::active", self.on_power)
            header.pack_start(self.power)
            view.add_top_bar(header)

            self.banner = Adw.Banner()
            view.add_top_bar(self.banner)
            self.update_banner()

            page = Adw.PreferencesPage()
            view.set_content(page)
            self.set_content(view)

            # --- Preview
            g = Adw.PreferencesGroup()
            frame = Gtk.Box(css_classes=["preview-frame"])
            self.preview = KeyboardPreview(self)
            frame.append(self.preview)
            g.add(frame)
            page.add(g)

            # --- Effect
            g = Adw.PreferencesGroup(title="Effect")
            self.effect_row = Adw.ComboRow(title="Mode",
                                           model=Gtk.StringList.new([n for _, n in EFFECTS]))
            self.effect_row.set_selected([k for k, _ in EFFECTS].index(self.cfg["effect"]))
            self.effect_row.connect("notify::selected", self.on_effect)
            g.add(self.effect_row)
            self.speed_row, self.speed = self.slider_row("Speed", 1, 100, self.cfg["speed"],
                                                        self.on_speed)
            g.add(self.speed_row)
            page.add(g)

            # --- Color
            self.color_group = g = Adw.PreferencesGroup(title="Color")
            if self.nzones > 1:
                zrow = Adw.ActionRow(title="Apply to")
                box = Gtk.Box(css_classes=["linked"], valign=Gtk.Align.CENTER)
                first = None
                for i, label in enumerate(["All"] + [f"Zone {n + 1}" for n in range(self.nzones)]):
                    b = Gtk.ToggleButton(label=label, active=(i == 0))
                    if first:
                        b.set_group(first)
                    else:
                        first = b
                    b.connect("toggled", self.on_zone, None if i == 0 else i - 1)
                    box.append(b)
                zrow.add_suffix(box)
                g.add(zrow)

            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=10,
                               min_children_per_line=9, column_spacing=4, row_spacing=8,
                               margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
                               homogeneous=True)
            self.swatches = []
            for i, (name, rgb) in enumerate(PRESETS):
                b = Gtk.Button(css_classes=["swatch", f"swatch-{i}"], tooltip_text=name,
                               halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda _b, c=rgb: self.set_color(list(c)))
                self.swatches.append((b, list(rgb)))
                flow.append(b)
            swatch_row = Adw.PreferencesRow(activatable=False)
            swatch_row.set_child(flow)
            g.add(swatch_row)

            crow = Adw.ActionRow(title="Custom color")
            self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                                   valign=Gtk.Align.CENTER)
            self.color_btn.connect("notify::rgba", self.on_custom_color)
            crow.add_suffix(self.color_btn)
            g.add(crow)
            page.add(g)

            # --- Brightness
            g = Adw.PreferencesGroup(title="Brightness")
            row, self.bright = self.slider_row("Level", 1, 100, self.cfg["brightness"],
                                               self.on_brightness)
            g.add(row)
            page.add(g)

            self.sync_widgets()
            GLib.timeout_add(1000, self.poll_external)
            self._mtime = config_mtime()

        # ---------- widget helpers
        def slider_row(self, title, lo, hi, value, cb):
            row = Adw.ActionRow(title=title)
            s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
            s.set_value(value)
            s.set_hexpand(True)
            s.set_size_request(240, -1)
            s.set_draw_value(True)
            s.set_value_pos(Gtk.PositionType.RIGHT)
            s.set_format_value_func(lambda _s, v: f"{int(v)}%")
            s.connect("value-changed", cb)
            row.add_suffix(s)
            return row, s

        def update_banner(self):
            if not self.kb.kind:
                self.banner.set_title("Keyboard driver not found — demo mode. "
                                      "Run: sudo ./install-driver.sh")
                self.banner.set_revealed(True)
            elif not self.kb.writable():
                self.banner.set_title("No permission to control the keyboard. "
                                      "Run: sudo ./install-driver.sh (installs the udev rule)")
                self.banner.set_revealed(True)
            else:
                self.banner.set_revealed(False)

        def current_color(self):
            return self.cfg["zones"][self.zone_sel or 0]

        def sync_widgets(self):
            self._updating = True
            eff = self.cfg["effect"]
            self.speed_row.set_sensitive(eff in ANIMATED)
            self.color_group.set_sensitive(self.kind == "rgb" and eff not in ("spectrum", "wave"))
            cur = self.current_color()
            for b, rgb in self.swatches:
                (b.add_css_class if rgb == cur else b.remove_css_class)("selected")
            rgba = Gdk.RGBA()
            rgba.parse("#%02x%02x%02x" % tuple(cur))
            self.color_btn.set_rgba(rgba)
            self.power.set_active(self.cfg["on"])
            self.bright.set_value(self.cfg["brightness"])
            self._updating = False
            self.preview.queue_draw()

        def commit(self):
            save_config(self.cfg)
            self._mtime = config_mtime()
            self.sync_widgets()

        # ---------- callbacks
        def on_power(self, sw, _p):
            if not self._updating:
                self.cfg["on"] = sw.get_active()
                self.commit()

        def on_effect(self, row, _p):
            if not self._updating:
                self.cfg["effect"] = EFFECTS[row.get_selected()][0]
                self.commit()

        def on_speed(self, s):
            if not self._updating:
                self.cfg["speed"] = int(s.get_value())
                self.commit()

        def on_brightness(self, s):
            if not self._updating:
                self.cfg["brightness"] = int(s.get_value())
                self.cfg["on"] = True
                self.commit()

        def on_zone(self, btn, idx):
            if btn.get_active():
                self.zone_sel = idx
                self.sync_widgets()

        def on_custom_color(self, btn, _p):
            if self._updating:
                return
            c = btn.get_rgba()
            self.set_color([round(c.red * 255), round(c.green * 255), round(c.blue * 255)])

        def set_color(self, rgb):
            if self.zone_sel is None:
                self.cfg["zones"] = [list(rgb) for _ in range(3)]
            else:
                self.cfg["zones"][self.zone_sel] = list(rgb)
            if self.cfg["effect"] in ("spectrum", "wave"):
                self.cfg["effect"] = "static"
                self._updating = True
                self.effect_row.set_selected(0)
                self._updating = False
            self.commit()

        def poll_external(self):
            # The daemon may change the config (e.g. brightness changed with the Fn keys).
            m = config_mtime()
            if m != self._mtime:
                self._mtime = m
                self.cfg = load_config()
                self.sync_widgets()
            if not self.kb.kind and self.kb.detect():
                self.update_banner()
            return GLib.SOURCE_CONTINUE

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID)

        def do_startup(self):
            Adw.Application.do_startup(self)
            prov = Gtk.CssProvider()
            prov.load_from_data(CSS.encode())
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                      Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        def do_activate(self):
            win = self.props.active_window or Window(self)
            win.present()

    if not os.path.exists(CONFIG_PATH):
        save_config(load_config())
    ensure_daemon()
    return App().run([sys.argv[0]])


if __name__ == "__main__":
    if "--daemon" in sys.argv:
        run_daemon(tray="--no-tray" not in sys.argv)
    elif "--toggle" in sys.argv:
        toggle()
    elif "--remove-shortcut" in sys.argv:
        Shortcut.remove()
    elif "--status" in sys.argv:
        sys.exit(print_status())
    else:
        sys.exit(run_gui())
