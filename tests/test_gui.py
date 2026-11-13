"""Settings window smoke tests. Need a display (CI runs them under xvfb-run)."""

import os
import unittest

from helpers import IsolatedTestCase, have_gi

from keyboard_rgb import config


def have_display() -> bool:
    if not have_gi() or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk

        Adw.init()
        return Gdk.Display.get_default() is not None
    except Exception:
        return False


@unittest.skipUnless(have_display(), "no display / GTK available")
class WindowTests(IsolatedTestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        from gi.repository import Adw, Gio

        cls.app = Adw.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
        cls.app.register(None)

    def window(self):
        from keyboard_rgb import gui

        win = gui.Window(self.app)
        self.addCleanup(win.destroy)
        return win

    def test_three_zone_keyboard(self):
        self.leds.rgb_zones(3)
        win = self.window()
        self.assertEqual(win.nzones, 3)
        self.assertTrue(win.color_group.get_visible())
        self.assertTrue(win.effect_row.get_sensitive())

    def test_color_change_is_written(self):
        self.leds.rgb_zones(3)
        win = self.window()
        win.set_color([1, 2, 3])
        self.assertEqual(win.cfg["zones"][0], [1, 2, 3])  # applied locally right away
        win._flush()
        self.assertEqual(config.load()["zones"], [[1, 2, 3]] * 3)

    def test_per_zone_color(self):
        self.leds.rgb_zones(3)
        win = self.window()
        win.zone_sel = 1
        win.set_color([9, 9, 9])
        win._flush()
        self.assertEqual(config.load()["zones"], [[255, 255, 255], [9, 9, 9], [255, 255, 255]])

    def test_window_does_not_overwrite_changes_made_elsewhere(self):
        """Regression: the window used to save its whole (stale) copy of the config."""
        self.leds.rgb_zones(1)
        win = self.window()
        config.update(effect="flash", shortcut_installed=True)  # tray / daemon, window not polled yet
        win.change(brightness=10)
        win._flush()
        cfg = config.load()
        self.assertEqual((cfg["effect"], cfg["brightness"], cfg["shortcut_installed"]), ("flash", 10, True))

    def test_white_keyboard_hides_color_controls(self):
        self.leds.add("white:kbd_backlight", rgb=False, max_brightness="2")
        win = self.window()
        self.assertFalse(win.color_group.get_visible())
        self.assertFalse(win.effect_row.get_sensitive())

    def test_banners(self):
        win = self.window()  # no hardware
        self.assertTrue(win.banner.get_revealed())
        self.assertIn("No supported keyboard", win.banner.get_title())

        self.leds.rgb_zones(1)
        win = self.window()  # hardware, but no daemon running
        self.assertTrue(win.banner.get_revealed())
        self.assertIn("background service", win.banner.get_title())

    def test_selected_swatch_reflects_color(self):
        self.leds.rgb_zones(3)
        config.update(zones=[[255, 0, 0], [0, 255, 0], [0, 0, 255]])
        win = self.window()
        selected = [rgb for b, rgb in win.swatches if b.has_css_class("selected")]
        self.assertEqual(selected, [])  # "All" with different zone colors: nothing selected
        win.zone_sel = 0
        win.sync_widgets()
        selected = [rgb for b, rgb in win.swatches if b.has_css_class("selected")]
        self.assertEqual(selected, [[255, 0, 0]])

    def test_preview_draws_every_effect(self):
        from gi.repository import Gtk

        self.leds.rgb_zones(3)
        win = self.window()
        for eff in ("static", "breathing", "spectrum", "wave", "flash"):
            win.cfg = dict(win.cfg, effect=eff)
            snap = Gtk.Snapshot()
            win.preview.do_snapshot(snap)
            snap.to_node()
