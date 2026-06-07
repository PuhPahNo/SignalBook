"""Settlement parsing against REAL captured AiScore final-match pages.

These fixtures are verbatim page text scraped from finished baseball and
tennis matches (tests/fixtures/pages/). They are the ground truth that was
missing when tennis/baseball settlement silently failed: every signal piled
up "open" because the finished-page markup never parsed into a final score.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.aiscore import (
    _is_final_state,
    _parse_detail_scoreboard,
    _tennis_total_games,
)
from quicklin_soccer.cli import _settlement_skip_reason

PAGES = ROOT / "tests" / "fixtures" / "pages"


def settle_result(text: str, home: str, away: str, sport: str) -> dict:
    """Reconstruct the fields _generic_match_result derives from a page, so we
    can exercise the settlement gate end-to-end without a live browser."""
    detail = _parse_detail_scoreboard(text, home, away, sport)
    home_score, away_score = detail if detail else (0, 0)
    return {
        "home_score": home_score,
        "away_score": away_score,
        "score_source": "detail_scoreboard" if detail else "nuxt_match",
        "is_final": _is_final_state(text, sport),
        "status_id": None,
        "minute": 0,
    }


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


# (fixture, home_team, away_team, expected (home, away) final score)
BASEBALL_CASES = [
    ("baseball_bbc-grosetto-macerata.txt", "BBC Grosetto", "Macerata", (6, 2)),
    ("baseball_ostrava-sabat-prague.txt", "Ostrava", "SaBaT Prague", (8, 4)),
    ("baseball_paris-la-rochelle.txt", "Paris", "La Rochelle", (9, 10)),
]

# (fixture, expected total games home+away)
TENNIS_CASES = [
    ("tennis_max-alcala-gurri-buvaysar-gadamauri.txt", (14, 8)),
    ("tennis_oscar-corwin-ashton-adesoro.txt", (7, 12)),
    ("tennis_tygen-goldammer-louis-hull.txt", (15, 18)),
]


class BaseballSettlementTests(unittest.TestCase):
    def test_finished_baseball_parses_run_totals(self):
        for fixture, home, away, expected in BASEBALL_CASES:
            with self.subTest(fixture=fixture):
                score = _parse_detail_scoreboard(page(fixture), home, away, "baseball")
                self.assertEqual(score, expected)

    def test_finished_baseball_is_final(self):
        for fixture, *_ in BASEBALL_CASES:
            with self.subTest(fixture=fixture):
                self.assertTrue(_is_final_state(page(fixture), "baseball"))


class TennisSettlementTests(unittest.TestCase):
    def test_finished_tennis_sums_games_not_sets(self):
        # The headline scoreboard shows sets won (e.g. 2-1); the bet settles on
        # total GAMES. _parse_detail_scoreboard must return summed games.
        for fixture, expected in TENNIS_CASES:
            with self.subTest(fixture=fixture):
                score = _parse_detail_scoreboard(page(fixture), "Home", "Away", "tennis")
                self.assertEqual(score, expected)
                self.assertEqual(_tennis_total_games(page(fixture)), expected)

    def test_finished_tennis_is_final_despite_set_labels(self):
        # Regression: set-column labels (S1/S2/S3) used to force is_final=False.
        for fixture, _ in TENNIS_CASES:
            with self.subTest(fixture=fixture):
                self.assertTrue(_is_final_state(page(fixture), "tennis"))

    def test_tennis_without_set_summaries_returns_none(self):
        self.assertIsNone(_tennis_total_games("Player A\nFull Time\nPlayer B\nno sets here"))


class SettlementGateEndToEndTests(unittest.TestCase):
    """The actual bug was the gate: these matches must now be settlement-ready
    with the right final total, where before they were stuck 'open' forever."""

    def test_baseball_pages_are_now_settlement_ready(self):
        for fixture, home, away, expected in BASEBALL_CASES:
            with self.subTest(fixture=fixture):
                result = settle_result(page(fixture), home, away, "baseball")
                self.assertIsNone(_settlement_skip_reason("baseball", result))
                self.assertEqual(result["home_score"] + result["away_score"], sum(expected))

    def test_tennis_pages_are_now_settlement_ready_on_games(self):
        for fixture, expected in TENNIS_CASES:
            with self.subTest(fixture=fixture):
                result = settle_result(page(fixture), "Home", "Away", "tennis")
                self.assertIsNone(_settlement_skip_reason("tennis", result))
                self.assertEqual(result["home_score"] + result["away_score"], sum(expected))


class FinalStateBoilerplateTests(unittest.TestCase):
    def test_live_page_not_marked_final_by_boilerplate(self):
        # A live basketball page carries "...halftime or final result." in its
        # boilerplate; the live clock must keep it from grading as final.
        text = (
            "Army Basket Club\n70\nQ4 04:23\n35\nAbidjan Basket Club\n"
            "AiScore provides quarter results, halftime or final result."
        )
        self.assertFalse(_is_final_state(text, "basketball", None))


if __name__ == "__main__":
    unittest.main()
