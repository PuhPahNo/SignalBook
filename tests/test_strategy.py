from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.models import HistoricalProfile, MatchStats
from quicklin_soccer.strategy import evaluate_match, needs_history


def make_stats(**overrides) -> MatchStats:
    data = {
        "url": "https://www.aiscore.com/match-demo/demo",
        "home_team": "Home",
        "away_team": "Away",
        "minute": 50,
        "home_score": 1,
        "away_score": 0,
        "home_attacks": 20,
        "away_attacks": 20,
        "home_dangerous_attacks": 10,
        "away_dangerous_attacks": 10,
        "home_shots_on_target": 4,
        "away_shots_on_target": 2,
        "home_shots_off_target": 3,
        "away_shots_off_target": 2,
        "home_corners": 2,
        "away_corners": 1,
    }
    data.update(overrides)
    return MatchStats(**data)


class StrategyTests(unittest.TestCase):
    def test_needs_history_for_high_pace_live_game(self):
        self.assertTrue(needs_history(make_stats()))

    def test_ignores_games_after_80_minutes(self):
        self.assertFalse(needs_history(make_stats(minute=81)))

    def test_full_time_over_signal(self):
        history = HistoricalProfile(
            final_goal_totals=(2, 2, 3, 4, 1, 2, 3, 2),
            halftime_goal_totals=(1, 0, 2, 1, 1, 1, 0, 2),
        )
        signal = evaluate_match(make_stats(), history)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.pick, "Full Time Over")
        self.assertEqual(signal.model_total_line, 1.5)

    def test_first_half_under_signal(self):
        history = HistoricalProfile(
            final_goal_totals=(0, 1, 1, 2, 0, 1),
            halftime_goal_totals=(0, 0, 0, 1, 0, 0),
        )
        stats = make_stats(
            minute=25,
            home_score=0,
            away_score=0,
            home_shots_on_target=0,
            away_shots_on_target=0,
            home_shots_off_target=1,
            away_shots_off_target=1,
        )
        signal = evaluate_match(stats, history)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.pick, "First Half Under")


if __name__ == "__main__":
    unittest.main()
