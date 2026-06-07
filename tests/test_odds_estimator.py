from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.models import LiveSnapshot, canonical_match_id
from quicklin_soccer.odds_estimator import (
    DEFAULT_VIG,
    OddsEstimator,
    _add_vig,
    estimated_odds_to_quotes,
)
from quicklin_soccer.sports import (
    SPORT_BASEBALL,
    SPORT_BASKETBALL,
    SPORT_HOCKEY,
    SPORT_SOCCER,
)


def snapshot(sport: str, **overrides) -> LiveSnapshot:
    data = {
        "provider": "test",
        "provider_match_id": f"{sport}-1",
        "canonical_id": canonical_match_id("Home", "Away", "2026-05-23T12:00:00+00:00", sport),
        "url": "https://example.test/match",
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "home_team": "Home",
        "away_team": "Away",
        "league": "Test League",
        "minute": 60,
        "status": "live",
        "home_score": 1,
        "away_score": 1,
        "home_attacks": 0,
        "away_attacks": 0,
        "home_dangerous_attacks": 0,
        "away_dangerous_attacks": 0,
        "home_shots_on_target": 6,
        "away_shots_on_target": 5,
        "home_shots_off_target": 0,
        "away_shots_off_target": 0,
        "home_corners": 0,
        "away_corners": 0,
        "sport": sport,
        "phase": "P2",
        "clock": "10:00",
        "stats": {"shots_on_goal": 11},
    }
    data.update(overrides)
    return LiveSnapshot(**data)


# Snapshots chosen to land each sport mid-game with a real two-sided market.
_SPORT_SNAPSHOTS = {
    SPORT_SOCCER: snapshot(SPORT_SOCCER, minute=60, home_score=1, away_score=1),
    SPORT_HOCKEY: snapshot(SPORT_HOCKEY, minute=30, home_score=2, away_score=1, phase="P2"),
    SPORT_BASKETBALL: snapshot(SPORT_BASKETBALL, minute=24, home_score=50, away_score=48, phase="Q2"),
    SPORT_BASEBALL: snapshot(SPORT_BASEBALL, minute=4, home_score=3, away_score=2, phase="inning_4"),
}


class AddVigTests(unittest.TestCase):
    def test_vig_produces_positive_overround(self):
        """A real book's implied probabilities sum to MORE than 1 — that
        overround is the house edge. Regression guard for the inverted-vig
        bug where dividing by (1+vig) synthesized a book paying out >100%,
        handing every estimated signal a phantom ~vig of positive EV."""
        over_decimal, under_decimal = _add_vig(0.6, 0.4, DEFAULT_VIG)
        implied_sum = 1.0 / over_decimal + 1.0 / under_decimal
        self.assertGreater(implied_sum, 1.0)
        self.assertAlmostEqual(implied_sum, 1.0 + DEFAULT_VIG, places=2)

    def test_offered_odds_are_worse_than_fair(self):
        # Offered decimal odds must be shorter (smaller) than the no-vig fair
        # price 1/p on each side — the bettor is paid less than fair.
        over_decimal, under_decimal = _add_vig(0.6, 0.4, DEFAULT_VIG)
        self.assertLess(over_decimal, 1.0 / 0.6)
        self.assertLess(under_decimal, 1.0 / 0.4)


class EstimatorOverroundTests(unittest.TestCase):
    def test_every_sport_estimate_has_positive_overround(self):
        estimator = OddsEstimator()
        for sport, snap in _SPORT_SNAPSHOTS.items():
            with self.subTest(sport=sport):
                estimated = estimator.estimate(snap, sport)
                self.assertIsNotNone(estimated, f"{sport} produced no estimate")
                implied_sum = 1.0 / estimated.over_odds + 1.0 / estimated.under_odds
                self.assertGreater(
                    implied_sum, 1.0,
                    f"{sport} synthesized a book paying out >100% ({implied_sum:.4f})",
                )

    def test_estimator_cannot_manufacture_positive_ev(self):
        """You cannot extract a real edge from a price you synthesized from
        your own model. Evaluated against the estimator's OWN fair
        probabilities, both sides must be non-positive EV (the vig makes them
        slightly negative). Before the fix this was +DEFAULT_VIG on both."""
        estimator = OddsEstimator()
        for sport, snap in _SPORT_SNAPSHOTS.items():
            with self.subTest(sport=sport):
                est = estimator.estimate(snap, sport)
                self.assertIsNotNone(est)
                ev_over = est.fair_over_probability * est.over_odds - 1.0
                ev_under = est.fair_under_probability * est.under_odds - 1.0
                self.assertLessEqual(ev_over, 1e-6, f"{sport} over EV positive: {ev_over:.4f}")
                self.assertLessEqual(ev_under, 1e-6, f"{sport} under EV positive: {ev_under:.4f}")

    def test_quotes_carry_overround_into_odds_quotes(self):
        estimator = OddsEstimator()
        snap = _SPORT_SNAPSHOTS[SPORT_SOCCER]
        est = estimator.estimate(snap, SPORT_SOCCER)
        over_quote, under_quote = estimated_odds_to_quotes(est, snap, SPORT_SOCCER)
        implied_sum = 1.0 / over_quote.decimal_odds + 1.0 / under_quote.decimal_odds
        self.assertGreater(implied_sum, 1.0)
        self.assertTrue(over_quote.is_estimated)
        self.assertTrue(under_quote.is_estimated)


if __name__ == "__main__":
    unittest.main()
