from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.calibration import (  # noqa: E402
    CalibrationResult,
    baseball_metrics_from_mlb_hitting,
    basketball_metrics_from_nba_stats,
    hockey_metrics_from_nhl_summary,
)
from quicklin_soccer.storage import QuicklinStore  # noqa: E402


class CalibrationTests(unittest.TestCase):
    def test_hockey_metrics_from_nhl_summary(self):
        metrics = hockey_metrics_from_nhl_summary(
            {
                "data": [
                    {
                        "gamesPlayed": 10,
                        "goalsFor": 30,
                        "goalsAgainst": 24,
                        "shotsForPerGame": 32,
                        "shotsAgainstPerGame": 29,
                    },
                    {
                        "gamesPlayed": 10,
                        "goalsFor": 20,
                        "goalsAgainst": 26,
                        "shotsForPerGame": 28,
                        "shotsAgainstPerGame": 31,
                    },
                ]
            }
        )

        self.assertEqual(metrics["teams"], 2)
        self.assertEqual(metrics["estimated_game_total_goals"], 5.0)
        self.assertEqual(metrics["shots_for_per_team_game"], 30.0)

    def test_basketball_metrics_from_nba_stats(self):
        base_data = {
            "resultSets": [
                {
                    "headers": ["TEAM_ID", "TEAM_NAME", "GP", "PTS", "FGA", "FG3A", "FTA", "TOV"],
                    "rowSet": [
                        [1, "A", 10, 100, 85, 30, 20, 12],
                        [2, "B", 10, 110, 90, 35, 25, 14],
                    ],
                }
            ]
        }
        advanced_data = {
            "resultSets": [
                {
                    "headers": ["TEAM_ID", "TEAM_NAME", "GP", "PACE"],
                    "rowSet": [[1, "A", 10, 98], [2, "B", 10, 102]],
                }
            ]
        }

        metrics = basketball_metrics_from_nba_stats(base_data, advanced_data)

        self.assertEqual(metrics["teams"], 2)
        self.assertEqual(metrics["estimated_game_total_points"], 210.0)
        self.assertEqual(metrics["pace"], 100.0)

    def test_baseball_metrics_from_mlb_hitting(self):
        metrics = baseball_metrics_from_mlb_hitting(
            {
                "stats": [
                    {
                        "splits": [
                            {"stat": {"gamesPlayed": 10, "runs": 50, "hits": 90, "homeRuns": 10, "baseOnBalls": 20}},
                            {"stat": {"gamesPlayed": 10, "runs": 40, "hits": 80, "homeRuns": 8, "baseOnBalls": 16}},
                        ]
                    }
                ]
            }
        )

        self.assertEqual(metrics["teams"], 2)
        self.assertEqual(metrics["runs_per_team_game"], 4.5)
        self.assertEqual(metrics["estimated_game_total_runs"], 9.0)

    def test_store_latest_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            with QuicklinStore(Path(directory) / "quicklin.db") as store:
                store.insert_calibration_run(
                    CalibrationResult(
                        sport="hockey",
                        strategy_version="hockey_totals_v1",
                        source="fixture",
                        source_url="https://example.test",
                        season="fixture",
                        status="calibrated_baseline",
                        sample_size=2,
                        metrics={"estimated_game_total_goals": 5.0},
                    )
                )
                latest = store.latest_calibration("hockey")
                latest_success = store.latest_successful_calibration("hockey")

        self.assertIsNotNone(latest)
        self.assertIsNotNone(latest_success)
        self.assertEqual(latest["status"], "calibrated_baseline")
        self.assertEqual(latest_success["status"], "calibrated_baseline")


if __name__ == "__main__":
    unittest.main()
