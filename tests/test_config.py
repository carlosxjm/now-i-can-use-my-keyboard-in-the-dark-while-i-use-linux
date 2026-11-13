import json
import os
import threading

from helpers import IsolatedTestCase

from keyboard_rgb import config


class ValidateTests(IsolatedTestCase):
    def test_defaults_when_not_a_dict(self):
        for data in (None, [], "x", 5):
            self.assertEqual(config.validate(data), config.defaults())

    def test_invalid_values_fall_back_to_defaults(self):
        cfg = config.validate({"brightness": "x", "speed": None, "on": "no",
                               "effect": "disco", "zones": 5, "shortcut_installed": "yes"})
        self.assertEqual(cfg, config.defaults())

    def test_out_of_range_values_are_clamped(self):
        cfg = config.validate({"brightness": 250, "speed": -3, "zones": [[999, -5, 3]]})
        self.assertEqual(cfg["brightness"], 100)
        self.assertEqual(cfg["speed"], 1)
        self.assertEqual(cfg["zones"][0], [255, 0, 3])

    def test_booleans_are_not_numbers(self):
        self.assertEqual(config.validate({"brightness": True})["brightness"], 100)

    def test_numeric_strings_and_floats_are_accepted(self):
        cfg = config.validate({"brightness": "40", "speed": 33.6})
        self.assertEqual((cfg["brightness"], cfg["speed"]), (40, 34))

    def test_bad_zone_entries_inherit_previous_zone(self):
        cfg = config.validate({"zones": [[10, 20, 30], ["a", 1, 2], [1, 2]]})
        self.assertEqual(cfg["zones"][0], [10, 20, 30])
        self.assertEqual(cfg["zones"][1], [10, 1, 2])  # only the bad channel is replaced
        self.assertEqual(cfg["zones"][2], [10, 1, 2])  # wrong length: copy of previous zone

    def test_missing_zones_are_padded(self):
        cfg = config.validate({"zones": [[1, 2, 3]]})
        self.assertEqual(cfg["zones"], [[1, 2, 3]] * 3)

    def test_unknown_keys_dropped_and_format_unchanged(self):
        cfg = config.validate({"on": False, "foo": 1})
        self.assertNotIn("foo", cfg)
        self.assertEqual(set(cfg), {"on", "effect", "brightness", "speed", "zones", "shortcut_installed"})

    def test_int_zero_one_accepted_as_bool(self):
        self.assertIs(config.validate({"on": 0})["on"], False)


class FileTests(IsolatedTestCase):
    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load(), config.defaults())

    def test_save_load_roundtrip(self):
        cfg = config.defaults()
        cfg.update(on=False, effect="wave", brightness=42)
        config.save(cfg)
        self.assertEqual(config.load(), cfg)

    def test_reads_files_written_by_older_versions(self):
        os.makedirs(config.config_dir())
        old = {"on": True, "effect": "breathing", "brightness": 85, "speed": 80,
               "zones": [[255, 0, 140], [150, 0, 255], [0, 60, 255]]}
        with open(config.config_path(), "w") as fh:
            json.dump(old, fh)
        cfg = config.load()
        self.assertEqual({k: cfg[k] for k in old}, old)
        self.assertFalse(cfg["shortcut_installed"])

    def test_corrupt_file_gives_defaults_and_is_backed_up(self):
        os.makedirs(config.config_dir())
        with open(config.config_path(), "w") as fh:
            fh.write("{not json")
        with self.assertLogs("keyboard_rgb.config", "WARNING"):
            self.assertEqual(config.load(), config.defaults())
        with open(config.config_path() + ".corrupt") as fh:
            self.assertEqual(fh.read(), "{not json")

    def test_atomic_write_leaves_no_temp_files(self):
        for i in range(5):
            config.update(brightness=i + 10)
        leftovers = [f for f in os.listdir(config.config_dir()) if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_update_only_changes_given_keys(self):
        config.update(effect="flash")
        config.update(brightness=30)  # a second writer must not reset the effect
        cfg = config.load()
        self.assertEqual((cfg["effect"], cfg["brightness"]), ("flash", 30))

    def test_update_validates(self):
        self.assertEqual(config.update(brightness=500)["brightness"], 100)

    def test_concurrent_updates_are_not_lost(self):
        config.ensure_exists()
        keys = [("brightness", 11), ("speed", 22), ("effect", "wave"), ("on", False)]
        threads = [threading.Thread(target=lambda k=k, v=v: [config.update(**{k: v}) for _ in range(20)])
                   for k, v in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        cfg = config.load()
        for k, v in keys:
            self.assertEqual(cfg[k], v)

    def test_ensure_exists_does_not_overwrite(self):
        config.update(brightness=7)
        config.ensure_exists()
        self.assertEqual(config.load()["brightness"], 7)

    def test_mtime_changes_on_write(self):
        self.assertEqual(config.mtime(), 0)
        config.update(on=False)
        self.assertGreater(config.mtime(), 0)
