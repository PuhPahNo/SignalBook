"""Odds parsing + coherence guardrail.

The live "listing row" parser mis-paired numbers into impossible two-sided
books (implied probabilities summing to ~0.70), which manufactured huge phantom
EV and made the bot bet both sides of the same total. The fix sources the
in-play total off the match detail page and refuses any incoherent book. These
tests pin both against real captured pages and the known multi-sport format.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.aiscore import _parse_detail_total_odds
from quicklin_soccer.models import is_coherent_two_sided_odds

PAGES = ROOT / "tests" / "fixtures" / "pages"


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


# Minimal real-format detail snippets per sport (label + primary/secondary
# triples + the next market's spillover line), mirroring captured pages.
BASKETBALL_PAGE = "Total Points\n142.5\n1.83\n1.83\n137.5\n1.83\n1.83\n95.5\n1\n2"
HOCKEY_PAGE = "Total Goals\n6.5\n1.90\n1.90\n5.5\n2.10\n1.72\n3.5"
SUSPENDED_PAGE = "Total Points\n-\n-\n-\n-\n-\n-\n-\n1\n2\n3"


class DetailTotalOddsTests(unittest.TestCase):
    def test_real_baseball_page_parses_coherent_in_play_total(self):
        odds = _parse_detail_total_odds(page("live_baseball_colorado_with_odds.txt"), "baseball")
        self.assertEqual(odds, (12.5, 1.86, 1.8))

    def test_suspended_baseball_market_returns_none(self):
        # Arizona's Total Runs was all dashes at capture — no real market.
        self.assertIsNone(_parse_detail_total_odds(page("live_baseball_arizona_no_odds.txt"), "baseball"))

    def test_basketball_first_triple_is_primary_line(self):
        self.assertEqual(_parse_detail_total_odds(BASKETBALL_PAGE, "basketball"), (142.5, 1.83, 1.83))

    def test_hockey_total_goals(self):
        self.assertEqual(_parse_detail_total_odds(HOCKEY_PAGE, "hockey"), (6.5, 1.90, 1.90))

    def test_dashes_market_returns_none(self):
        self.assertIsNone(_parse_detail_total_odds(SUSPENDED_PAGE, "basketball"))

    def test_missing_label_returns_none(self):
        self.assertIsNone(_parse_detail_total_odds("no odds here\n1.5\n2.0", "baseball"))


class CoherenceGuardrailTests(unittest.TestCase):
    def test_rejects_the_old_listing_garbage(self):
        # The exact incoherent pairs the listing parser used to emit.
        self.assertFalse(is_coherent_two_sided_odds(2.64, 3.22))  # implied 0.689
        self.assertFalse(is_coherent_two_sided_odds(2.71, 3.00))  # implied 0.702
        self.assertFalse(is_coherent_two_sided_odds(2.90, 2.83))  # implied 0.698

    def test_accepts_real_books(self):
        self.assertTrue(is_coherent_two_sided_odds(1.86, 1.80))  # implied 1.093
        self.assertTrue(is_coherent_two_sided_odds(1.90, 1.90))  # implied 1.052
        self.assertTrue(is_coherent_two_sided_odds(1.83, 1.83))

    def test_rejects_degenerate_odds(self):
        self.assertFalse(is_coherent_two_sided_odds(None, 1.9))
        self.assertFalse(is_coherent_two_sided_odds(1.0, 1.9))
        self.assertFalse(is_coherent_two_sided_odds(-1.0, 1.9))


if __name__ == "__main__":
    unittest.main()
