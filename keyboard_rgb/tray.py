"""Tray icon: StatusNotifierItem + DBusMenu, spoken directly over D-Bus.

There is no AppIndicator library for GTK4 (and the daemon doesn't use GTK at
all), so this implements the two small D-Bus interfaces the GNOME AppIndicator
extension (and KDE, etc.) consume:

  org.kde.StatusNotifierItem  icon, tooltip, scroll (brightness), middle click (toggle)
  com.canonical.dbusmenu      the menu (on/off, effect, color, brightness, settings, quit)

`build_menu()` is a pure function so the menu can be tested without a bus.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Mapping, Protocol

from . import APP_NAME, PACKAGE
from .effects import EFFECT_NAMES, EFFECTS, PRESETS, SELF_COLORED
from .icons import ICON_SIZES, icon_key, keyboard_icon_rgba, rgba_to_argb, swatch_png

log = logging.getLogger(__name__)

BRIGHTNESS_LEVELS = (10, 25, 50, 75, 100)
SCROLL_STEP = 5


class Actions(Protocol):
    def change(self, **kw: Any) -> None: ...
    def set_color(self, rgb: list[int]) -> None: ...
    def open_settings(self) -> None: ...
    def quit(self) -> None: ...


# --------------------------------------------------------------------------- menu model
Node = dict[str, Any]


def build_menu(cfg: Mapping, actions: Actions, *, available: bool, color_ok: bool,
               error: str | None = None, shortcut_label: str | None = None,
               ) -> tuple[Node, dict[int, Node]]:
    """Build the DBusMenu tree. Returns (root, {id: node}); the root has id 0."""
    items: dict[int, Node] = {}
    counter = [0]

    def node(label: str | None = None, action: Callable[[], None] | None = None,
             children: list[Node] | None = None, **props: Any) -> Node:
        counter[0] += 1
        n: Node = {"id": counter[0], "props": dict(props), "action": action,
                   "children": children or []}
        if label is not None:
            n["props"]["label"] = label
        if children:
            n["props"]["children-display"] = "submenu"
        items[n["id"]] = n
        return n

    def sep() -> Node:
        return node(type="separator")

    def radio(label: str, active: bool, action: Callable[[], None], **props: Any) -> Node:
        return node(label, action, **{"toggle-type": "radio", "toggle-state": int(active)}, **props)

    cur = list(cfg["zones"][0])
    effects = [radio(name, cfg["effect"] == key, lambda k=key: actions.change(effect=k))
               for key, name in EFFECTS]
    colors = [radio(name, cfg["effect"] not in SELF_COLORED and list(c) == cur,
                    lambda c=c: actions.set_color(list(c)), **{"icon-data": swatch_png(16, c)})
              for name, c in PRESETS]
    levels = [radio(f"{v}%", cfg["brightness"] == v,
                    lambda v=v: actions.change(brightness=v, on=True))
              for v in BRIGHTNESS_LEVELS]

    on_label = f"On   ({shortcut_label})" if shortcut_label else "On"
    children: list[Node] = []
    if not available:
        children += [node("No supported keyboard detected", enabled=False), sep()]
    elif error:
        children += [node(f"Error: {error}", enabled=False), sep()]
    children += [
        node(on_label, lambda: actions.change(on=not cfg["on"]),
             **{"toggle-type": "checkmark", "toggle-state": int(cfg["on"])}),
        sep(),
        node("Effect", children=effects, enabled=color_ok),
        node("Color", children=colors, enabled=color_ok),
        node("Brightness", children=levels),
        sep(),
        node("Settings…", actions.open_settings),
        node("Quit", actions.quit),
    ]
    root = node(children=children)
    del items[root["id"]]
    root["id"] = 0
    items[0] = root
    return root, items


def tooltip_text(cfg: Mapping, available: bool, error: str | None) -> str:
    if not available:
        return "No supported keyboard detected"
    if error:
        return f"Error: {error}"
    if not cfg["on"]:
        return "Off"
    return f"{EFFECT_NAMES[cfg['effect']]} · {cfg['brightness']}%"


# --------------------------------------------------------------------------- D-Bus
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

PROP_TYPES = {"label": "s", "enabled": "b", "visible": "b", "type": "s", "toggle-type": "s",
              "toggle-state": "i", "children-display": "s", "icon-name": "s", "icon-data": "ay"}
ERR_INVALID = "org.freedesktop.DBus.Error.InvalidArgs"
ERR_UNKNOWN = "org.freedesktop.DBus.Error.UnknownMethod"
ERR_FAILED = "org.freedesktop.DBus.Error.Failed"


class MenuServer:
    """DBusMenu + StatusNotifierItem method/property handlers (bus-independent)."""

    def __init__(self, owner: Any) -> None:
        from gi.repository import GLib

        self.GLib = GLib
        self.owner = owner  # the Daemon: provides cfg, tray_state() and the Actions
        self.revision = 1
        self.root: Node = {}
        self.items: dict[int, Node] = {}
        self.pixmaps: list[tuple[int, int, bytes]] = []
        self._icon_key: tuple | None = None
        self._tooltip = ""
        self.rebuild()

    # ------------------------------------------------------------- state
    def rebuild(self) -> tuple[bool, bool]:
        """Recompute menu/icon/tooltip. Returns (icon_changed, tooltip_changed)."""
        st = self.owner.tray_state()
        cfg = self.owner.cfg
        self.root, self.items = build_menu(cfg, self.owner, **st)
        self.revision += 1
        key = icon_key(cfg, st["available"])
        icon_changed = key != self._icon_key
        if icon_changed:
            self._icon_key = key
            self.pixmaps = [(s, s, rgba_to_argb(keyboard_icon_rgba(s, key))) for s in ICON_SIZES]
        tip = tooltip_text(cfg, st["available"], st["error"])
        tip_changed = tip != self._tooltip
        self._tooltip = tip
        return icon_changed, tip_changed

    def props_variant(self, n: Node, names: list[str] | None = None) -> dict:
        V = self.GLib.Variant
        return {k: V(PROP_TYPES[k], v) for k, v in n["props"].items()
                if (not names or k in names) and k in PROP_TYPES}

    def layout(self, n: Node, depth: int, names: list[str]) -> tuple:
        V = self.GLib.Variant
        kids = []
        if depth != 0:
            kids = [V("(ia{sv}av)", self.layout(c, depth - 1, names)) for c in n["children"]]
        return (n["id"], self.props_variant(n, names), kids)

    # ------------------------------------------------------------- StatusNotifierItem
    def sni_property(self, prop: str):
        V = self.GLib.Variant
        return {
            "Category": V("s", "Hardware"),
            "Id": V("s", PACKAGE),
            "Title": V("s", APP_NAME),
            "Status": V("s", "Active"),
            "IconName": V("s", ""),
            "IconPixmap": V("a(iiay)", self.pixmaps),
            "IconThemePath": V("s", ""),
            "ToolTip": V("(sa(iiay)ss)", ("", [], APP_NAME, self._tooltip)),
            "ItemIsMenu": V("b", True),
            "Menu": V("o", Tray.MENU_PATH),
        }.get(prop)

    def sni_method(self, method: str, args: tuple) -> tuple[str | None, Any]:
        """Returns (dbus_error_name or None, return Variant or None)."""
        if method == "SecondaryActivate":  # middle click: toggle on/off
            self.owner.change(on=not self.owner.cfg["on"])
        elif method == "Scroll":  # scroll wheel over the icon: brightness
            delta, orientation = args
            if orientation not in ("vertical", "horizontal"):
                return ERR_INVALID, None
            if delta and orientation == "vertical":
                b = self.owner.cfg["brightness"] + (-SCROLL_STEP if delta > 0 else SCROLL_STEP)
                self.owner.change(brightness=max(SCROLL_STEP, min(100, b)), on=True)
        elif method == "ContextMenu":
            pass  # the menu is shown by the host (ItemIsMenu)
        else:
            return ERR_UNKNOWN, None
        return None, None

    # ------------------------------------------------------------- DBusMenu
    def menu_property(self, prop: str):
        V = self.GLib.Variant
        return {"Version": V("u", 3), "TextDirection": V("s", "ltr"),
                "Status": V("s", "normal"), "IconThemePath": V("as", [])}.get(prop)

    def menu_method(self, method: str, args: tuple) -> tuple[str | None, Any]:
        V = self.GLib.Variant
        if method == "GetLayout":
            parent, depth, names = args
            if parent not in self.items:
                return ERR_INVALID, None
            depth = -1 if depth < 0 else min(depth, 16)
            return None, V("(u(ia{sv}av))", (self.revision, self.layout(self.items[parent], depth, names)))
        if method == "GetGroupProperties":
            ids, names = args
            res = [(i, self.props_variant(self.items[i], names)) for i in ids if i in self.items]
            return None, V("(a(ia{sv}))", (res,))
        if method == "GetProperty":
            i, name = args
            if i not in self.items or name not in self.items[i]["props"] or name not in PROP_TYPES:
                return ERR_INVALID, None
            return None, V("(v)", (V(PROP_TYPES[name], self.items[i]["props"][name]),))
        if method == "Event":
            i, event = args[0], args[1]
            if i not in self.items:
                return ERR_INVALID, None
            self.event(i, event)
            return None, None
        if method == "EventGroup":
            errors = []
            for i, event, *_ in args[0]:
                if i in self.items:
                    self.event(i, event)
                else:
                    errors.append(i)
            if errors and len(errors) == len(args[0]):
                return ERR_INVALID, None
            return None, V("(ai)", (errors,))
        if method == "AboutToShow":
            if args[0] not in self.items:
                return ERR_INVALID, None
            return None, V("(b)", (False,))
        if method == "AboutToShowGroup":
            errors = [i for i in args[0] if i not in self.items]
            return None, V("(aiai)", ([], errors))
        return ERR_UNKNOWN, None

    def event(self, i: int, event: str) -> None:
        n = self.items.get(i)
        if event == "clicked" and n and n["action"]:
            action = n["action"]
            # run outside the D-Bus handler so the shell gets its reply immediately
            self.GLib.idle_add(lambda: (action(), False)[1])


class Tray:
    """Exports MenuServer on the session bus and registers with the watcher."""

    SNI_PATH = "/StatusNotifierItem"
    MENU_PATH = "/MenuBar"
    SNI_IFACE = "org.kde.StatusNotifierItem"
    MENU_IFACE = "com.canonical.dbusmenu"

    def __init__(self, owner: Any) -> None:
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.server = MenuServer(owner)
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        sni = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(DBUSMENU_XML).interfaces[0]
        self._reg = [
            self.conn.register_object(self.SNI_PATH, sni, self._call, self._prop, None),
            self.conn.register_object(self.MENU_PATH, menu, self._call, self._prop, None),
        ]
        self._own = Gio.bus_own_name_on_connection(self.conn, self.name,
                                                   Gio.BusNameOwnerFlags.NONE, None, None)
        # Register with the StatusNotifierWatcher, and again whenever it (re)appears,
        # e.g. after GNOME Shell restarts
        self._watch = Gio.bus_watch_name_on_connection(
            self.conn, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            self._register, lambda *_: log.info("tray host went away; waiting for it"))

    def _register(self, conn, name, owner) -> None:
        def done(c, res):
            try:
                c.call_finish(res)
                log.info("tray icon registered")
            except Exception as e:  # host refused: log, keep running without the icon
                log.warning("tray registration failed: %s", e)

        conn.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                  "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                  self.GLib.Variant("(s)", (self.name,)), None, self.Gio.DBusCallFlags.NONE,
                  -1, None, done)

    def _prop(self, conn, sender, path, iface, prop):
        if iface == self.SNI_IFACE:
            return self.server.sni_property(prop)
        return self.server.menu_property(prop)

    def _call(self, conn, sender, path, iface, method, params, inv) -> None:
        try:
            args = params.unpack()
            handler = self.server.sni_method if iface == self.SNI_IFACE else self.server.menu_method
            err, ret = handler(method, args)
        except Exception as e:  # never let a bad call take the daemon down
            log.exception("D-Bus call %s.%s failed", iface, method)
            inv.return_dbus_error(ERR_FAILED, str(e))
            return
        if err:
            inv.return_dbus_error(err, f"invalid call to {method}")
        else:
            inv.return_value(ret)

    def update(self) -> None:
        icon_changed, tip_changed = self.server.rebuild()
        emit = self.conn.emit_signal
        if icon_changed:
            emit(None, self.SNI_PATH, self.SNI_IFACE, "NewIcon", None)
        if tip_changed:
            emit(None, self.SNI_PATH, self.SNI_IFACE, "NewToolTip", None)
        emit(None, self.MENU_PATH, self.MENU_IFACE, "LayoutUpdated",
             self.GLib.Variant("(ui)", (self.server.revision, 0)))

    def close(self) -> None:
        for reg in self._reg:
            self.conn.unregister_object(reg)
        self.Gio.bus_unown_name(self._own)
        self.Gio.bus_unwatch_name(self._watch)
