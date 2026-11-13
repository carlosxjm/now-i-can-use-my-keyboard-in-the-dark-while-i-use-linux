import os
import struct
import subprocess
import sys
import unittest
import zlib

from helpers import REPO, IsolatedTestCase, have_gi

from keyboard_rgb import config, icons
from keyboard_rgb.shortcut import pretty


class IconTests(unittest.TestCase):
    def test_icon_sizes(self):
        for size in icons.ICON_SIZES:
            self.assertEqual(len(icons.keyboard_icon_rgba(size, ("off",))), size * size * 4)

    def test_icon_key(self):
        cfg = config.defaults()
        self.assertEqual(icons.icon_key(cfg), ("zones", (255, 255, 255)))
        self.assertEqual(icons.icon_key(dict(cfg, on=False)), ("off",))
        self.assertEqual(icons.icon_key(cfg, available=False), ("off",))
        self.assertEqual(icons.icon_key(dict(cfg, effect="wave")), ("rainbow",))
        self.assertEqual(len(icons.icon_key(dict(cfg, zones=[[1, 1, 1], [2, 2, 2], [3, 3, 3]]))), 4)

    def test_icon_uses_the_color(self):
        px = icons.keyboard_icon_rgba(22, ("zones", (10, 20, 30)))
        opaque = {tuple(px[i:i + 3]) for i in range(0, len(px), 4) if px[i + 3] == 255}
        self.assertEqual(opaque, {(10, 20, 30)})

    def test_png_is_valid(self):
        png = icons.swatch_png(16, (255, 0, 0))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        pos, chunks = 8, {}
        while pos < len(png):
            (length,) = struct.unpack(">I", png[pos:pos + 4])
            tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
            (crc,) = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])
            self.assertEqual(crc, zlib.crc32(tag + data) & 0xFFFFFFFF)
            chunks[tag] = data
            pos += 12 + length
        self.assertEqual(struct.unpack(">II", chunks[b"IHDR"][:8]), (16, 16))
        self.assertEqual(len(zlib.decompress(chunks[b"IDAT"])), 16 * (16 * 4 + 1))

    def test_argb_conversion(self):
        self.assertEqual(icons.rgba_to_argb(bytes([1, 2, 3, 4])), bytes([4, 1, 2, 3]))


class ShortcutTests(IsolatedTestCase):
    def test_pretty(self):
        self.assertEqual(pretty("<Control><Super>k"), "Ctrl+Super+K")
        self.assertEqual(pretty("<Primary><Shift>F5"), "Ctrl+Shift+F5")
        self.assertEqual(pretty("<Super>space"), "Super+Space")
        self.assertIsNone(pretty(""))
        self.assertIsNone(pretty(None))
        self.assertIsNone(pretty("<Super>"))

    def _gnome_schema(self):
        if not have_gi():
            return False
        from gi.repository import Gio

        src = Gio.SettingsSchemaSource.get_default()
        return bool(src and src.lookup("org.gnome.settings-daemon.plugins.media-keys", True))

    def test_registration_lifecycle(self):
        if not self._gnome_schema():
            self.skipTest("GNOME settings schema not installed")
        from keyboard_rgb import shortcut

        os.environ["KEYBOARD_RGB_LAUNCHER"] = os.path.join(REPO, "bin", "avell-kbd")
        marked, changes = [], []
        s = shortcut.Shortcut(False, lambda: marked.append(1), lambda: changes.append(1))
        self.assertEqual(marked, [1])
        self.assertEqual(s.label(), "Ctrl+Super+K")
        self.assertEqual(shortcut.current_label(), "Ctrl+Super+K")
        self.assertTrue(s.entry.get_string("command").endswith("avell-kbd --toggle"))
        # user deletes it: not recreated on the next start
        s.parent.set_strv("custom-keybindings", [])
        s2 = shortcut.Shortcut(True, lambda: marked.append(2), lambda: None)
        self.assertIsNone(s2.label())
        self.assertEqual(marked, [1])
        shortcut.remove()
        self.assertIsNone(shortcut.current_label())

    def test_no_launcher_means_no_shortcut(self):
        if not self._gnome_schema():
            self.skipTest("GNOME settings schema not installed")
        from keyboard_rgb import shortcut

        marked = []
        s = shortcut.Shortcut(False, lambda: marked.append(1), lambda: None)
        self.assertIsNone(s.label())
        self.assertEqual(marked, [])


class CliTests(IsolatedTestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-I", os.path.join(REPO, "bin", "avell-kbd"), *args],
                              capture_output=True, text=True, timeout=30)

    def test_version_and_help(self):
        from keyboard_rgb import __version__

        self.assertIn(__version__, self.run_cli("--version").stdout)
        self.assertIn("--toggle", self.run_cli("--help").stdout)

    def test_toggle(self):
        self.assertEqual(self.run_cli("--toggle").stdout.strip(), "off")
        self.assertFalse(config.load()["on"])
        self.assertEqual(self.run_cli("--toggle").stdout.strip(), "on")

    def test_toggle_keeps_other_settings(self):
        config.update(effect="flash", brightness=33)
        self.run_cli("--toggle")
        cfg = config.load()
        self.assertEqual((cfg["effect"], cfg["brightness"]), ("flash", 33))

    def test_status_without_hardware(self):
        r = self.run_cli("--status")
        self.assertEqual(r.returncode, 1)
        self.assertIn("No supported keyboard", r.stdout)

    def test_status_with_hardware(self):
        self.leds.rgb_zones(3)
        r = self.run_cli("--status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("zones: 3", r.stdout)
        self.assertIn("Background service: not running", r.stdout)

    def test_conflicting_modes_rejected(self):
        self.assertEqual(self.run_cli("--toggle", "--status").returncode, 2)
