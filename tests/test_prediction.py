"""Win-rate prediction engine: per-sport checkpoint + directional over/under
call against a reference line (no odds, no EV)."""
from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.models import LiveSnapshot, canonical_match_id
from quicklin_soccer.strategy import at_checkpoint, checkpoint_minute, predict_total


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
        "minute": 4,
        "status": "live",
        "home_score": 1,
        "away_score": 1,
        "home_attacks": 0,
        "away_attacks": 0,
        "home_dangerous_attacks": 0,
        "away_dangerous_attacks": 0,
        "home_shots_on_target": 3,
        "away_shots_on_target": 2,
        "home_shots_off_target": 0,
        "away_shots_off_target": 0,
        "home_corners": 0,
        "away_corners": 0,
        "sport": sport,
        "phase": "inning_4",
        "clock": None,
        "stats": {},
    }
    data.update(overrides)
    return LiveSnapshot(**data)


class CheckpointTests(unittest.TestCase):
    def test_checkpoint_values(self):
        self.assertEqual(checkpoint_minute("soccer"), 45)
        self.assertEqual(checkpoint_minute("baseball"), 4)

    def test_at_checkpoint_gates_on_game_stage(self):
        self.assertFalse(at_checkpoint(snapshot("soccer", minute=30, phase="P1")))
        self.assertTrue(at_checkpoint(snapshot("soccer", minute=45, phase="P2")))
        self.assertFalse(at_checkpoint(snapshot("baseball", minute=3, phase="inning_3")))
        self.assertTrue(at_checkpoint(snapshot("baseball", minute=4, phase="inning_4")))


class PredictTotalTests(unittest.TestCase):
    def test_returns_valid_directional_call(self):
        pred = predict_total(snapshot("baseball", minute=4, home_score=1, away_score=1, phase="inning_4"), 8.5)
        self.assertIn(pred.side, ("over", "under"))
        self.assertEqual(pred.line, 8.5)
        self.assertGreaterEqual(pred.confidence, 0.5)  # confidence is the larger side's prob
        self.assertLessEqual(pred.confidence, 1.0)
        self.assertGreaterEqual(pred.over_probability, 0.0)
        self.assertLessEqual(pred.over_probability, 1.0)

    def test_already_decided_line_predicts_that_side_with_high_confidence(self):
        # Game already 11 runs vs a 8.5 line: over is settled -> over, ~certain.
        pred = predict_total(snapshot("baseball", minute=8, home_score=6, away_score=5, phase="inning_8"), 8.5)
        self.assertEqual(pred.side, "over")
        self.assertGreater(pred.confidence, 0.95)
        self.assertGreater(pred.over_probability, 0.95)

    def test_low_total_far_below_line_predicts_under(self):
        # Late game, only 2 runs, line 9.5 -> under is near-certain.
        pred = predict_total(snapshot("baseball", minute=8, home_score=1, away_score=1, phase="inning_8"), 9.5)
        self.assertEqual(pred.side, "under")
        self.assertGreater(pred.confidence, 0.9)

    def test_over_and_under_probabilities_are_complementary_on_half_line(self):
        pred = predict_total(snapshot("basketball", minute=24, home_score=50, away_score=48, phase="Q2", clock="00:00"), 210.5)
        # Half-line: no push, so P(predicted) ~= max(over, 1-over).
        self.assertAlmostEqual(pred.confidence, max(pred.over_probability, 1 - pred.over_probability), places=2)


if __name__ == "__main__":
    unittest.main()
