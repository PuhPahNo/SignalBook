"""Pinnacle odds source, tested against real captured guest-API JSON.

Pinnacle is the live, sharp odds source that replaces AiScore's frozen
pre-match total. Fixtures are verbatim guest-API responses for two live MLB
games (tests/fixtures/pinnacle/).
"""
from __future__ import annotations

import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.models import is_coherent_two_sided_odds
from quicklin_soccer.pinnacle import (
    PinnacleClient,
    american_to_decimal,
    find_game,
    main_total_from_markets,
    teams_match,
    to_odds_quotes,
)

FX = ROOT / "tests" / "fixtures" / "pinnacle"
PADRES = 1631758085
DODGERS = 1631758084


def fixture_fetch(path: str):
    mapping = {
        "/sports": "sports.json",
        "/sports/3/matchups": "baseball_matchups.json",
        f"/matchups/{PADRES}/markets/related/straight": f"markets_{PADRES}.json",
        f"/matchups/{DODGERS}/markets/related/straight": f"markets_{DODGERS}.json",
    }
    if path not in mapping:
        raise AssertionError(f"unexpected fetch path: {path}")
    return json.loads((FX / mapping[path]).read_text())


def snapshot_stub():
    return types.SimpleNamespace(
        provider_match_id="aiscore-padres-mets",
        canonical_id="cid",
        captured_at="2026-06-07T20:30:00+00:00",
    )


class AmericanToDecimalTests(unittest.TestCase):
    def test_conversions(self):
        self.assertEqual(american_to_decimal(-108), 1.9259)
        self.assertEqual(american_to_decimal(100), 2.0)
        self.assertEqual(american_to_decimal(384), 4.84)
        self.assertEqual(american_to_decimal(-100), 2.0)

    def test_bad_values(self):
        self.assertIsNone(american_to_decimal(0))
        self.assertIsNone(american_to_decimal(None))
        self.assertIsNone(american_to_decimal("x"))


class TeamMatchTests(unittest.TestCase):
    def test_exact_and_substring_and_token(self):
        self.assertTrue(teams_match("New York Mets", "New York Mets"))
        self.assertTrue(teams_match("NY Mets", "New York Mets"))
        self.assertTrue(teams_match("San Diego Padres", "Padres"))
        self.assertTrue(teams_match("Los Angeles Dodgers", "LA Dodgers"))

    def test_non_match(self):
        self.assertFalse(teams_match("San Diego Padres", "New York Mets"))
        self.assertFalse(teams_match("", "New York Mets"))


class PinnacleClientTests(unittest.TestCase):
    def setUp(self):
        self.client = PinnacleClient(fetch=fixture_fetch)

    def test_sport_id(self):
        self.assertEqual(self.client.sport_id("baseball"), 3)
        self.assertEqual(self.client.sport_id("soccer"), 29)

    def test_live_games(self):
        games = self.client.live_games("baseball")
        self.assertEqual(len(games), 2)
        padres = next(g for g in games if g.matchup_id == PADRES)
        self.assertEqual(padres.home, "San Diego Padres")
        self.assertEqual(padres.away, "New York Mets")
        self.assertEqual(padres.league, "MLB")
        self.assertTrue(padres.is_live)

    def test_find_game_maps_aiscore_names(self):
        games = self.client.live_games("baseball")
        # AiScore-style names that differ slightly still resolve.
        game = find_game("San Diego Padres", "NY Mets", games)
        self.assertIsNotNone(game)
        self.assertEqual(game.matchup_id, PADRES)

    def test_main_total_is_live_sharp_and_coherent(self):
        total = self.client.main_total(PADRES)
        self.assertIsNotNone(total)
        self.assertEqual(total.line, 7.5)
        self.assertEqual(total.over_decimal, 1.9259)  # -108
        self.assertEqual(total.under_decimal, 1.9615)  # -104
        self.assertTrue(is_coherent_two_sided_odds(total.over_decimal, total.under_decimal))

    def test_main_total_skips_alternate_lines(self):
        # Dodgers main line is 8.5 (non-alternate), not the 5.5/7.0 alternates.
        total = self.client.main_total(DODGERS)
        self.assertEqual(total.line, 8.5)


class MarketHelpersTests(unittest.TestCase):
    def test_no_total_market_returns_none(self):
        self.assertIsNone(main_total_from_markets([{"type": "moneyline", "period": 0}]))

    def test_suspended_main_total_returns_none(self):
        markets = [{"type": "total", "period": 0, "isAlternate": False, "prices": [{"designation": "over", "points": 7.5, "price": -105}]}]
        self.assertIsNone(main_total_from_markets(markets))

    def test_to_odds_quotes(self):
        total = self.client = PinnacleClient(fetch=fixture_fetch).main_total(PADRES)
        over, under = to_odds_quotes(total, snapshot_stub(), "baseball")
        self.assertEqual(over.side, "over")
        self.assertEqual(under.side, "under")
        self.assertEqual(over.line, 7.5)
        self.assertEqual(over.market, "total_runs")
        self.assertEqual(over.bookmaker, "pinnacle")
        self.assertTrue(is_coherent_two_sided_odds(over.decimal_odds, under.decimal_odds))


if __name__ == "__main__":
    unittest.main()
