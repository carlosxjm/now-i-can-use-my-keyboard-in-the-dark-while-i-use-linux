"""Tray icon over a real, private D-Bus session bus (tests/run.sh, CI).

Exercises the actual marshalling of every method the AppIndicator host calls.
"""

import os
import unittest

from helpers import IsolatedTestCase, have_gi

from keyboard_rgb import config


def have_private_bus() -> bool:
    # Opt-in, so running the suite on a desktop never touches the real session bus
    # (tests/run.sh sets this inside dbus-run-session).
    return (have_gi() and bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))
            and os.environ.get("KEYBOARD_RGB_TEST_PRIVATE_BUS") == "1")


@unittest.skipUnless(have_private_bus(), "needs a private D-Bus session bus (use tests/run.sh)")
class TrayBusTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        from gi.repository import Gio, GLib

        from keyboard_rgb.daemon import Daemon

        self.Gio, self.GLib = Gio, GLib
        self.leds.rgb_zones(1)
        self.d = Daemon(tray=True, shortcut=False)
        self.assertIsNotNone(self.d.tray, "tray failed to start")
        self.addCleanup(self.d.tray.close)
        addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
        self.client = Gio.DBusConnection.new_for_address_sync(
            addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.addCleanup(self.client.close_sync, None)
        self.dest = self.d.tray.conn.get_unique_name()

    def pump(self, cond, limit=2000):
        ctx = self.GLib.MainContext.default()
        for _ in range(limit):
            if cond():
                return
            ctx.iteration(True)
        self.fail("timed out")

    def call(self, path, iface, method, sig=None, args=None):
        box = {}
        params = self.GLib.Variant(sig, args) if sig else None

        def done(conn, res):
            try:
                box["v"] = conn.call_finish(res)
            except Exception as e:
                box["e"] = e

        self.client.call(self.dest, path, iface, method, params, None,
                         self.Gio.DBusCallFlags.NONE, 5000, None, done)
        self.pump(lambda: box)
        if "e" in box:
            raise box["e"]
        return box["v"].unpack()

    def menu(self, method, sig=None, args=None):
        return self.call("/MenuBar", "com.canonical.dbusmenu", method, sig, args)

    def sni(self, method, sig=None, args=None):
        return self.call("/StatusNotifierItem", "org.kde.StatusNotifierItem", method, sig, args)

    def prop(self, path, iface, name):
        return self.call(path, "org.freedesktop.DBus.Properties", "Get", "(ss)", (iface, name))[0]

    def flatten(self, node, out):
        out[node[1].get("label")] = node[0]
        for child in node[2]:
            self.flatten(child, out)
        return out

    def test_properties(self):
        sni = ("/StatusNotifierItem", "org.kde.StatusNotifierItem")
        self.assertEqual(self.prop(*sni, "Menu"), "/MenuBar")
        self.assertEqual(self.prop(*sni, "ToolTip")[2], "Keyboard RGB")
        self.assertEqual([p[0] for p in self.prop(*sni, "IconPixmap")], [16, 22, 32, 44])
        self.assertEqual(self.prop("/MenuBar", "com.canonical.dbusmenu", "Version"), 3)

    def test_layout_and_click(self):
        _rev, root = self.menu("GetLayout", "(iias)", (0, -1, []))
        ids = self.flatten(root, {})
        self.assertIn("Rainbow wave", ids)
        self.menu("Event", "(isvu)", (ids["Rainbow wave"], "clicked", self.GLib.Variant("i", 0), 0))
        self.pump(lambda: config.load()["effect"] == "wave")
        self.menu("EventGroup", "(a(isvu))", ([(ids["On"], "clicked", self.GLib.Variant("i", 0), 0)],))
        self.pump(lambda: config.load()["on"] is False)

    def test_group_properties_and_about_to_show(self):
        _rev, root = self.menu("GetLayout", "(iias)", (0, 1, ["label"]))
        ids = self.flatten(root, {})
        props = self.menu("GetGroupProperties", "(aias)", ([ids["On"]], ["label"]))[0]
        self.assertEqual(props[0][1]["label"], "On")
        self.assertEqual(self.menu("AboutToShow", "(i)", (0,)), (False,))
        self.assertEqual(self.menu("AboutToShowGroup", "(ai)", ([0, 4242],)), ([], [4242]))

    def test_invalid_calls_return_errors_and_daemon_survives(self):
        with self.assertRaises(self.GLib.Error):
            self.menu("GetLayout", "(iias)", (4242, -1, []))
        with self.assertRaises(self.GLib.Error):
            self.menu("Event", "(isvu)", (4242, "clicked", self.GLib.Variant("i", 0), 0))
        with self.assertRaises(self.GLib.Error):
            self.sni("Scroll", "(is)", (1, "sideways"))
        self.assertEqual(self.menu("GetLayout", "(iias)", (0, 0, []))[1][0], 0)  # still answering

    def test_scroll_and_middle_click(self):
        self.sni("Scroll", "(is)", (1, "vertical"))
        self.pump(lambda: config.load()["brightness"] == 95)
        self.sni("SecondaryActivate", "(ii)", (0, 0))
        self.pump(lambda: config.load()["on"] is False)
