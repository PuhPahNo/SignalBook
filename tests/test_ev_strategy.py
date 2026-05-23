from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.models import HistoricalProfile, LiveSnapshot, OddsQuote, canonical_match_id
from quicklin_soccer.strategy import StrategyConfig, evaluate_ev_totals


def make_snapshot(**overrides) -> LiveSnapshot:
    data = {
        "provider": "test",
        "provider_match_id": "abc",
        "canonical_id": canonical_match_id("Home", "Away", "2026-05-23T12:00:00+00:00"),
        "url": "https://example.test/match",
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "home_team": "Home",
        "away_team": "Away",
        "league": "Test League",
        "minute": 55,
        "status": "live",
        "home_score": 1,
        "away_score": 1,
        "home_attacks": 55,
        "away_attacks": 42,
        "home_dangerous_attacks": 32,
        "away_dangerous_attacks": 28,
        "home_shots_on_target": 4,
        "away_shots_on_target": 3,
        "home_shots_off_target": 6,
        "away_shots_off_target": 5,
        "home_corners": 4,
        "away_corners": 3,
    }
    data.update(overrides)
    return LiveSnapshot(**data)


def quote(side: str, odds: float, captured_at: str | None = None) -> OddsQuote:
    captured_at = captured_at or datetime.now(UTC).isoformat(timespec="seconds")
    return OddsQuote(
        provider="test",
        provider_match_id="abc",
        canonical_id="cid",
        captured_at=captured_at,
        market="total_goals",
        period="full_time",
        line=3.5,
        side=side,
        decimal_odds=odds,
    )


class EvStrategyTests(unittest.TestCase):
    def test_requires_two_sided_odds(self):
        result = evaluate_ev_totals(make_snapshot(), (quote("over", 2.1),))
        self.assertEqual(result.skip_reason, "missing_two_sided_total_odds")

    def test_rejects_stale_odds(self):
        stale = (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")
        result = evaluate_ev_totals(make_snapshot(), (quote("over", 2.1, stale), quote("under", 1.8, stale)))
        self.assertEqual(result.skip_reason, "stale_odds")

    def test_emits_positive_ev_signal(self):
        history = HistoricalProfile(final_goal_totals=(4, 5, 3, 4, 2, 5), halftime_goal_totals=(1, 2, 1, 0, 1, 2))
        result = evaluate_ev_totals(
            make_snapshot(),
            (quote("over", 2.6), quote("under", 1.55)),
            history,
            StrategyConfig(min_ev=0.01),
        )
        self.assertIsNotNone(result.prediction)
        self.assertTrue(result.signals)
        self.assertEqual(result.signals[0].side, "over")


if __name__ == "__main__":
    unittest.main()
