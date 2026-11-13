import unittest

from helpers import IsolatedTestCase, have_gi

from keyboard_rgb import config
from keyboard_rgb.tray import build_menu, tooltip_text


class FakeOwner:
    def __init__(self, **state):
        self.cfg = config.defaults()
        self.calls = []
        self.state = {"available": True, "color_ok": True, "error": None, "shortcut_label": None}
        self.state.update(state)

    def tray_state(self):
        return dict(self.state)

    def change(self, **kw):
        self.calls.append(("change", kw))
        self.cfg = config.validate({**self.cfg, **kw})

    def set_color(self, rgb):
        self.calls.append(("set_color", rgb))

    def open_settings(self):
        self.calls.append(("open_settings",))

    def quit(self):
        self.calls.append(("quit",))


def labels(root):
    return [c["props"].get("label") for c in root["children"]]


def find(items, label):
    return next(n for n in items.values() if n["props"].get("label") == label)


class MenuModelTests(unittest.TestCase):
    def build(self, **state):
        owner = FakeOwner(**state)
        root, items = build_menu(owner.cfg, owner, **owner.state)
        return owner, root, items

    def test_structure(self):
        _, root, items = self.build()
        self.assertEqual(root["id"], 0)
        self.assertIs(items[0], root)
        self.assertEqual(labels(root), ["On", None, "Effect", "Color", "Brightness", None, "Settings…", "Quit"])
        self.assertEqual(len(set(items)), len(items))

    def test_shortcut_shown_next_to_on(self):
        _, root, _ = self.build(shortcut_label="Ctrl+Super+K")
        self.assertEqual(labels(root)[0], "On   (Ctrl+Super+K)")

    def test_toggle_and_radio_states(self):
        owner, root, items = self.build()
        self.assertEqual(root["children"][0]["props"]["toggle-state"], 1)
        self.assertEqual(find(items, "Static")["props"]["toggle-state"], 1)
        self.assertEqual(find(items, "White")["props"]["toggle-state"], 1)
        self.assertEqual(find(items, "100%")["props"]["toggle-state"], 1)
        self.assertEqual(find(items, "50%")["props"]["toggle-state"], 0)

    def test_actions(self):
        owner, root, items = self.build()
        root["children"][0]["action"]()
        find(items, "Breathing")["action"]()
        find(items, "Blue")["action"]()
        find(items, "25%")["action"]()
        find(items, "Settings…")["action"]()
        self.assertEqual(owner.calls, [("change", {"on": False}), ("change", {"effect": "breathing"}),
                                       ("set_color", [0, 60, 255]),
                                       ("change", {"brightness": 25, "on": True}), ("open_settings",)])

    def test_color_menus_disabled_without_color_support(self):
        _, _, items = self.build(color_ok=False)
        self.assertFalse(find(items, "Effect")["props"]["enabled"])
        self.assertFalse(find(items, "Color")["props"]["enabled"])

    def test_missing_hardware_and_errors_are_shown(self):
        _, root, _ = self.build(available=False)
        self.assertEqual(labels(root)[0], "No supported keyboard detected")
        _, root, _ = self.build(error="cannot write brightness")
        self.assertEqual(labels(root)[0], "Error: cannot write brightness")

    def test_tooltip(self):
        cfg = config.defaults()
        self.assertEqual(tooltip_text(cfg, True, None), "Static · 100%")
        self.assertEqual(tooltip_text(dict(cfg, on=False), True, None), "Off")
        self.assertEqual(tooltip_text(cfg, False, None), "No supported keyboard detected")
        self.assertEqual(tooltip_text(cfg, True, "boom"), "Error: boom")


@unittest.skipUnless(have_gi(), "PyGObject not installed")
class DBusHandlerTests(IsolatedTestCase):
    """Method handlers, called the way the bus glue calls them (no real bus)."""

    def setUp(self):
        super().setUp()
        from gi.repository import GLib

        from keyboard_rgb.tray import ERR_INVALID, ERR_UNKNOWN, MenuServer

        self.GLib, self.ERR_INVALID, self.ERR_UNKNOWN = GLib, ERR_INVALID, ERR_UNKNOWN
        self.owner = FakeOwner()
        self.srv = MenuServer(self.owner)

    def call(self, method, *args):
        return self.srv.menu_method(method, args)

    def on_id(self):
        return find(self.srv.items, "On")["id"]

    def test_get_layout(self):
        err, v = self.call("GetLayout", 0, -1, [])
        self.assertIsNone(err)
        revision, (root_id, _props, children) = v.unpack()
        self.assertEqual(root_id, 0)
        self.assertEqual(children[0][1]["label"], "On")

    def test_get_layout_depth_and_property_filter(self):
        _, v = self.call("GetLayout", 0, 1, ["label"])
        _, (_, _, children) = v.unpack()
        effect = children[2]
        self.assertEqual(set(effect[1]), {"label"})
        self.assertEqual(effect[2], [])  # depth 1: submenus not expanded

    def test_get_layout_unknown_parent(self):
        self.assertEqual(self.call("GetLayout", 9999, -1, [])[0], self.ERR_INVALID)

    def test_get_property(self):
        err, v = self.call("GetProperty", self.on_id(), "label")
        self.assertIsNone(err)
        self.assertEqual(v.unpack()[0], "On")
        self.assertEqual(self.call("GetProperty", self.on_id(), "nonsense")[0], self.ERR_INVALID)
        self.assertEqual(self.call("GetProperty", 9999, "label")[0], self.ERR_INVALID)

    def test_group_properties_skip_unknown_ids(self):
        _, v = self.call("GetGroupProperties", [self.on_id(), 9999], [])
        self.assertEqual([i for i, _ in v.unpack()[0]], [self.on_id()])

    def test_event_runs_action_via_idle(self):
        err, _ = self.call("Event", self.on_id(), "clicked", self.GLib.Variant("i", 0), 0)
        self.assertIsNone(err)
        ctx = self.GLib.MainContext.default()
        while ctx.iteration(False):
            pass
        self.assertEqual(self.owner.calls, [("change", {"on": False})])

    def test_event_unknown_id_and_non_click_events(self):
        self.assertEqual(self.call("Event", 9999, "clicked", self.GLib.Variant("i", 0), 0)[0], self.ERR_INVALID)
        self.call("Event", self.on_id(), "hovered", self.GLib.Variant("i", 0), 0)
        ctx = self.GLib.MainContext.default()
        while ctx.iteration(False):
            pass
        self.assertEqual(self.owner.calls, [])

    def test_event_group_reports_unknown_ids(self):
        V = self.GLib.Variant
        err, v = self.call("EventGroup", [(9999, "clicked", V("i", 0), 0), (self.on_id(), "opened", V("i", 0), 0)])
        self.assertIsNone(err)
        self.assertEqual(v.unpack()[0], [9999])

    def test_about_to_show(self):
        self.assertFalse(self.call("AboutToShow", 0)[1].unpack()[0])
        self.assertEqual(self.call("AboutToShow", 9999)[0], self.ERR_INVALID)
        self.assertEqual(self.call("AboutToShowGroup", [0, 9999])[1].unpack(), ([], [9999]))

    def test_unknown_method(self):
        self.assertEqual(self.call("Explode")[0], self.ERR_UNKNOWN)
        self.assertEqual(self.srv.sni_method("Activate", (0, 0))[0], self.ERR_UNKNOWN)

    def test_scroll_validation_and_clamping(self):
        self.assertEqual(self.srv.sni_method("Scroll", (1, "diagonal"))[0], self.ERR_INVALID)
        self.srv.sni_method("Scroll", (0, "vertical"))
        self.srv.sni_method("Scroll", (5, "horizontal"))
        self.assertEqual(self.owner.calls, [])
        self.srv.sni_method("Scroll", (-2**31, "vertical"))  # up: brighter, capped at 100
        self.assertEqual(self.owner.cfg["brightness"], 100)
        for _ in range(40):
            self.srv.sni_method("Scroll", (2**31 - 1, "vertical"))
        self.assertEqual(self.owner.cfg["brightness"], 5)  # never scrolls to off

    def test_middle_click_toggles(self):
        self.srv.sni_method("SecondaryActivate", (0, 0))
        self.assertEqual(self.owner.calls, [("change", {"on": False})])

    def test_properties(self):
        self.assertEqual(self.srv.sni_property("Menu").unpack(), "/MenuBar")
        self.assertEqual(len(self.srv.sni_property("IconPixmap").unpack()), 4)
        self.assertIsNone(self.srv.sni_property("Nope"))
        self.assertEqual(self.srv.menu_property("Version").unpack(), 3)

    def test_rebuild_reports_icon_changes_only_when_needed(self):
        self.owner.cfg = dict(self.owner.cfg, brightness=40)
        icon_changed, tip_changed = self.srv.rebuild()
        self.assertFalse(icon_changed)  # brightness doesn't change the icon
        self.assertTrue(tip_changed)
        self.owner.cfg = dict(self.owner.cfg, zones=[[255, 0, 0]] * 3)
        self.assertTrue(self.srv.rebuild()[0])
