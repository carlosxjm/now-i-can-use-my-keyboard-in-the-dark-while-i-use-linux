import os

from helpers import IsolatedTestCase

from keyboard_rgb.hardware import ClevoSysfsBackend


class DetectionTests(IsolatedTestCase):
    def test_no_hardware(self):
        kb = ClevoSysfsBackend()
        self.assertFalse(kb.available)
        self.assertEqual(kb.nzones, 0)
        self.assertEqual(kb.supported_effects, frozenset())
        self.assertFalse(kb.set_brightness(50))
        self.assertFalse(kb.set_colors([[1, 2, 3]]))
        self.assertIsNone(kb.read_brightness_raw())
        self.assertFalse(kb.writable())

    def test_missing_sysfs_root(self):
        self.assertFalse(ClevoSysfsBackend(os.path.join(self.tmp, "nope")).available)

    def test_one_zone_rgb(self):
        self.leds.rgb_zones(1)
        kb = ClevoSysfsBackend()
        self.assertEqual((kb.kind, kb.nzones, kb.max_brightness), ("rgb", 1, 255))
        self.assertIn("wave", kb.supported_effects)

    def test_three_zone_rgb_in_order(self):
        self.leds.rgb_zones(3)
        kb = ClevoSysfsBackend()
        self.assertEqual(kb.nzones, 3)
        self.assertEqual([os.path.basename(z) for z in kb.zones],
                         ["rgb:kbd_backlight", "rgb:kbd_backlight_1", "rgb:kbd_backlight_2"])

    def test_white_only_keyboard_supports_static_only(self):
        self.leds.add("white:kbd_backlight", rgb=False, max_brightness="2")
        kb = ClevoSysfsBackend()
        self.assertEqual((kb.kind, kb.nzones), ("white", 1))
        self.assertFalse(kb.supports_color)
        self.assertEqual(kb.supported_effects, frozenset({"static"}))
        self.assertFalse(kb.set_colors([[1, 2, 3]]))
        self.assertTrue(kb.set_brightness(100))
        self.assertEqual(self.leds.read("white:kbd_backlight", "brightness"), "2")

    def test_devices_from_other_drivers_are_ignored(self):
        self.leds.add("dell::kbd_backlight", driver="dell_laptop", rgb=False)
        self.leds.add("rgb:kbd_backlight", driver="asus_wmi")
        self.leds.add("white:kbd_backlight", driver=None, rgb=False)
        self.assertFalse(ClevoSysfsBackend().available)

    def test_invalid_max_brightness_ignores_device(self):
        self.leds.rgb_zones(1, max_brightness="garbage")
        with self.assertLogs("keyboard_rgb.hardware", "WARNING"):
            self.assertFalse(ClevoSysfsBackend().available)

    def test_zero_max_brightness_ignores_device(self):
        self.leds.rgb_zones(1, max_brightness="0")
        with self.assertLogs("keyboard_rgb.hardware", "WARNING"):
            self.assertFalse(ClevoSysfsBackend().available)

    def test_unexpected_multi_index_disables_color(self):
        self.leds.rgb_zones(1, multi_index="red green")
        with self.assertLogs("keyboard_rgb.hardware", "WARNING"):
            kb = ClevoSysfsBackend()
        self.assertTrue(kb.available)
        self.assertFalse(kb.supports_color)


class IOTests(IsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.leds.rgb_zones(3)
        self.kb = ClevoSysfsBackend()

    def test_brightness_written_to_all_zones(self):
        self.assertTrue(self.kb.set_brightness(50))
        for name in ("rgb:kbd_backlight", "rgb:kbd_backlight_1", "rgb:kbd_backlight_2"):
            self.assertEqual(self.leds.read(name, "brightness"), "128")

    def test_brightness_conversion(self):
        kb = self.kb
        self.assertEqual(kb.brightness_raw(0), 0)
        self.assertEqual(kb.brightness_raw(1), 3)
        self.assertEqual(kb.brightness_raw(100), 255)
        self.assertEqual(kb.brightness_raw(150), 255)
        for pct in range(0, 101):
            self.assertEqual(kb.raw_to_pct(kb.brightness_raw(pct)), pct)

    def test_tiny_percentage_never_rounds_to_off(self):
        self.leds.remove("rgb:kbd_backlight_1")
        self.leds.remove("rgb:kbd_backlight_2")
        self.leds.write("rgb:kbd_backlight", "max_brightness", "2")
        kb = ClevoSysfsBackend()
        self.assertEqual(kb.brightness_raw(5), 1)

    def test_colors_written_per_zone_and_clamped(self):
        self.assertTrue(self.kb.set_colors([[255, 0, 0], [0, 255, 0], [300, -4, 7]]))
        self.assertEqual(self.leds.read("rgb:kbd_backlight", "multi_intensity"), "255 0 0")
        self.assertEqual(self.leds.read("rgb:kbd_backlight_1", "multi_intensity"), "0 255 0")
        self.assertEqual(self.leds.read("rgb:kbd_backlight_2", "multi_intensity"), "255 0 7")

    def test_channel_order_follows_multi_index(self):
        self.leds.remove("rgb:kbd_backlight_1")
        self.leds.remove("rgb:kbd_backlight_2")
        self.leds.write("rgb:kbd_backlight", "multi_index", "blue green red")
        kb = ClevoSysfsBackend()
        kb.set_colors([[10, 20, 30]])
        self.assertEqual(self.leds.read("rgb:kbd_backlight", "multi_intensity"), "30 20 10")

    def test_repeated_writes_are_cached_until_invalidated(self):
        self.kb.set_brightness(50)
        self.leds.write("rgb:kbd_backlight", "brightness", "7")  # someone else changed it
        self.kb.set_brightness(50)
        self.assertEqual(self.leds.read("rgb:kbd_backlight", "brightness"), "7")
        self.kb.invalidate()
        self.kb.set_brightness(50)
        self.assertEqual(self.leds.read("rgb:kbd_backlight", "brightness"), "128")

    def test_read_brightness_handles_garbage(self):
        self.leds.write("rgb:kbd_backlight", "brightness", "not-a-number")
        self.assertIsNone(self.kb.read_brightness_raw())

    def test_permission_denied_is_reported_not_raised(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores file permissions")
        os.chmod(os.path.join(self.leds.root, "rgb:kbd_backlight", "brightness"), 0o444)
        self.assertFalse(self.kb.writable())
        with self.assertLogs("keyboard_rgb.hardware", "ERROR"):
            self.assertFalse(self.kb.set_brightness(50))
        self.assertIn("Permission denied", self.kb.last_error)
        self.assertTrue(self.kb.available)  # still there, just not writable
        self.assertIsNone(self.kb.last_written_brightness_raw())

    def test_error_clears_after_successful_write(self):
        path = os.path.join(self.leds.root, "rgb:kbd_backlight", "brightness")
        if os.geteuid() == 0:
            self.skipTest("root ignores file permissions")
        os.chmod(path, 0o444)
        with self.assertLogs("keyboard_rgb.hardware", "ERROR"):
            self.kb.set_brightness(50)
        os.chmod(path, 0o644)
        self.assertTrue(self.kb.set_brightness(50))
        self.assertIsNone(self.kb.last_error)

    def test_vanished_device_becomes_unavailable_and_can_return(self):
        self.leds.remove("rgb:kbd_backlight")
        with self.assertLogs("keyboard_rgb.hardware", "WARNING"):
            self.assertFalse(self.kb.set_brightness(50))
        self.assertFalse(self.kb.available)
        self.leds.rgb_zones(1)  # driver reloaded
        self.assertTrue(self.kb.detect())

    def test_describe(self):
        d = self.kb.describe()
        self.assertEqual((d["kind"], d["zones"], d["writable"], d["error"]), ("rgb", 3, True, None))
