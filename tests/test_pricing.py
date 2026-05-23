from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.pricing import (
    expected_total_exposure,
    no_vig_probabilities,
    poisson_probability_at_or_above,
    settle_total_bet,
    split_total_line,
)


class PricingTests(unittest.TestCase):
    def test_no_vig_probabilities(self):
        over, under = no_vig_probabilities(1.91, 1.91)
        self.assertAlmostEqual(over, 0.5, places=4)
        self.assertAlmostEqual(under, 0.5, places=4)

    def test_quarter_line_split(self):
        self.assertEqual(split_total_line(2.25), ((2.0, 0.5), (2.5, 0.5)))
        self.assertEqual(split_total_line(2.75), ((2.5, 0.5), (3.0, 0.5)))

    def test_quarter_line_settlement(self):
        self.assertEqual(settle_total_bet(3, 2.75, "over", 2.0), ("half_win", 0.5))
        self.assertEqual(settle_total_bet(2, 2.25, "over", 2.0), ("half_loss", -0.5))
        self.assertEqual(settle_total_bet(2, 2.0, "under", 1.9), ("push", 0.0))

    def test_poisson_probability(self):
        self.assertGreater(poisson_probability_at_or_above(1.4, 1), 0.7)
        self.assertLess(poisson_probability_at_or_above(0.2, 2), 0.02)

    def test_expected_exposure_has_fair_odds(self):
        exposure = expected_total_exposure(2, 2.5, "over", 1.2)
        self.assertGreater(exposure.win_units, 0)
        self.assertGreater(exposure.fair_probability, 0)


if __name__ == "__main__":
    unittest.main()
