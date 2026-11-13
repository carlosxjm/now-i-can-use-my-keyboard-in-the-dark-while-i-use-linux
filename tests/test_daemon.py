import os
import unittest

from helpers import IsolatedTestCase, have_gi

from keyboard_rgb import config, status
from keyboard_rgb.hardware import ClevoSysfsBackend


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.w = 1_700_000_000.0

    def mono(self):
        return self.t

    def wall(self):
        return self.w

    def advance(self, s, wall=None):
        self.t += s
        self.w += s if wall is None else wall


@unittest.skipUnless(have_gi(), "PyGObject not installed")
class DaemonTests(IsolatedTestCase):
    def make(self, zones=1, **kw):
        from keyboard_rgb.daemon import Daemon

        if zones:
            self.leds.rgb_zones(zones, **kw)
        self.clock = Clock()
        self.d = Daemon(backend=ClevoSysfsBackend(), tray=False, shortcut=False,
                        monotonic=self.clock.mono, wall=self.clock.wall)
        return self.d

    def tick(self, seconds=0.1, **kw):
        self.clock.advance(seconds, **kw)
        return self.d.tick()

    def hw(self, attr, name="rgb:kbd_backlight"):
        return self.leds.read(name, attr)

    def test_applies_config_on_start(self):
        config.update(zones=[[255, 0, 0]] * 3, brightness=50)
        self.make()
        self.tick()
        self.assertEqual(self.hw("multi_intensity"), "255 0 0")
        self.assertEqual(self.hw("brightness"), "128")

    def test_off_sets_brightness_zero(self):
        self.make()
        self.d.change(on=False)
        self.tick()
        self.assertEqual(self.hw("brightness"), "0")
        self.assertFalse(config.load()["on"])

    def test_picks_up_config_written_by_others(self):
        self.make()
        self.tick()
        config.update(zones=[[0, 0, 255]] * 3)  # e.g. the settings window
        os.utime(config.config_path(), ns=(1, config.mtime() + 10_000_000))
        self.tick()
        self.assertEqual(self.hw("multi_intensity"), "0 0 255")

    def test_animated_effect_changes_colors_and_ticks_faster(self):
        config.update(effect="spectrum", speed=100)
        self.make()
        delay = self.tick()
        first = self.hw("multi_intensity")
        self.tick(0.2)
        self.assertNotEqual(self.hw("multi_intensity"), first)
        self.assertLess(delay, 100)

    def test_static_ticks_slower(self):
        self.make()
        self.assertGreaterEqual(self.tick(), 100)

    def test_external_brightness_change_is_adopted(self):
        self.make()
        self.tick(2)
        self.leds.write("rgb:kbd_backlight", "brightness", "64")  # Fn key
        self.tick(2)
        cfg = config.load()
        self.assertEqual((cfg["on"], cfg["brightness"]), (True, 25))

    def test_external_off_is_adopted(self):
        self.make()
        self.tick(2)
        self.leds.write("rgb:kbd_backlight", "brightness", "0")
        self.tick(2)
        self.assertFalse(config.load()["on"])

    def test_external_on_while_off_is_adopted(self):
        """Regression: Fn brightness-up while the app thought the light was off."""
        self.make()
        self.d.change(on=False)
        self.tick(2)
        self.leds.write("rgb:kbd_backlight", "brightness", "128")
        self.tick(2)
        cfg = config.load()
        self.assertEqual((cfg["on"], cfg["brightness"]), (True, 50))

    def test_resume_from_suspend_rewrites_state(self):
        self.make()
        self.tick()
        self.leds.write("rgb:kbd_backlight", "multi_intensity", "0 0 0")  # firmware reset
        self.tick(0.1, wall=600)  # wall clock jumped: we were asleep
        self.assertEqual(self.hw("multi_intensity"), "255 255 255")

    def test_survives_missing_hardware_and_finds_it_later(self):
        self.make(zones=0)
        self.assertFalse(self.d.kb.available)
        self.assertEqual(self.tick(), 500)
        self.leds.rgb_zones(1)  # driver loaded later
        self.tick(5)
        self.assertTrue(self.d.kb.available)
        self.tick()
        self.assertEqual(self.hw("brightness"), "255")

    def test_survives_hardware_disappearing(self):
        self.make()
        self.tick()
        self.leds.remove("rgb:kbd_backlight")
        self.d.change(brightness=10)
        with self.assertLogs("keyboard_rgb.hardware", "WARNING"):
            self.tick()
        self.assertFalse(self.d.kb.available)
        self.assertEqual(self.tick(), 500)

    def test_white_keyboard_runs_static_even_if_config_says_wave(self):
        config.update(effect="wave")
        self.leds.add("white:kbd_backlight", rgb=False, max_brightness="2")
        self.make(zones=0)
        self.assertEqual(self.d.effective_effect, "static")
        self.tick()
        self.assertEqual(self.hw("brightness", "white:kbd_backlight"), "2")
        self.assertEqual(config.load()["effect"], "wave")  # the user's choice is not rewritten

    def test_set_color_leaves_rainbow_effects(self):
        config.update(effect="wave")
        self.make()
        self.d.set_color([1, 2, 3])
        cfg = config.load()
        self.assertEqual((cfg["effect"], cfg["zones"][2]), ("static", [1, 2, 3]))

    def test_status_reports_hardware_errors(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file permissions")
        self.make()
        os.chmod(os.path.join(self.leds.root, "rgb:kbd_backlight", "brightness"), 0o444)
        self.d.change(brightness=20)
        with self.assertLogs("keyboard_rgb.hardware", "ERROR"):
            self.tick()
        st = status.read_raw()
        self.assertEqual(st["pid"], os.getpid())
        self.assertIn("Permission denied", st["error"])
        self.assertFalse(st["writable"])

    def test_status_read_ignores_dead_or_foreign_pids(self):
        status.write({"pid": 999999999})
        self.assertIsNone(status.read())
        status.write({"pid": os.getpid()})  # this test process isn't a daemon
        self.assertIsNone(status.read())
