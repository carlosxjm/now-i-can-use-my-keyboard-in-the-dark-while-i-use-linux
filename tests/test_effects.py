import unittest

import helpers  # noqa: F401  (sets sys.path)

from keyboard_rgb import config
from keyboard_rgb.effects import EFFECT_IDS, compute_colors, period_for


class EffectTests(unittest.TestCase):
    def cfg(self, **kw):
        c = config.defaults()
        c.update(zones=[[255, 0, 0], [0, 255, 0], [0, 0, 255]], **kw)
        return c

    def test_all_effects_produce_valid_rgb_for_any_zone_count(self):
        for eff in EFFECT_IDS:
            for n in (1, 2, 3):
                for t in (0, 0.13, 1.7, 33.3):
                    colors = compute_colors(self.cfg(effect=eff, speed=73), t, n)
                    self.assertEqual(len(colors), n, eff)
                    for c in colors:
                        self.assertEqual(len(c), 3)
                        self.assertTrue(all(isinstance(v, int) and 0 <= v <= 255 for v in c), (eff, c))

    def test_static_returns_zone_colors(self):
        self.assertEqual(compute_colors(self.cfg(), 5, 3), [[255, 0, 0], [0, 255, 0], [0, 0, 255]])

    def test_zero_zones_is_treated_as_one(self):
        self.assertEqual(len(compute_colors(self.cfg(), 0, 0)), 1)

    def test_breathing_dims_but_never_fully_off(self):
        cfg = self.cfg(effect="breathing", speed=1)
        lowest = compute_colors(cfg, 0, 1)[0]
        brightest = compute_colors(cfg, period_for(1) / 2, 1)[0]
        self.assertGreater(lowest[0], 0)
        self.assertLess(lowest[0], 20)
        self.assertEqual(brightest[0], 255)

    def test_flash_alternates(self):
        cfg = self.cfg(effect="flash", speed=1)
        p = period_for(1)
        self.assertEqual(compute_colors(cfg, 0.1 * p, 1), [[255, 0, 0]])
        self.assertEqual(compute_colors(cfg, 0.6 * p, 1), [[0, 0, 0]])

    def test_wave_zones_differ_and_spectrum_zones_match(self):
        wave = compute_colors(self.cfg(effect="wave"), 1.0, 3)
        self.assertEqual(len({tuple(c) for c in wave}), 3)
        spec = compute_colors(self.cfg(effect="spectrum"), 1.0, 3)
        self.assertEqual(len({tuple(c) for c in spec}), 1)

    def test_period_is_monotonic_and_clamped(self):
        self.assertAlmostEqual(period_for(1), 12.0)
        self.assertAlmostEqual(period_for(100), 0.6)
        self.assertGreater(period_for(10), period_for(90))
        self.assertEqual(period_for(-5), period_for(1))
        self.assertEqual(period_for(500), period_for(100))
