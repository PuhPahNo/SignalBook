from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.football_data import backtest_historical_baseline, import_football_data_csv
from quicklin_soccer.storage import QuicklinStore


class FootballDataTests(unittest.TestCase):
    def test_import_and_backtest_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = QuicklinStore(Path(tmp) / "quicklin.db")
            imported = import_football_data_csv(store, ROOT / "tests/fixtures/football_data_sample.csv", "sample")
            result = backtest_historical_baseline(store, min_ev=0.0, min_history=2)
            store.close()

        self.assertEqual(imported, 8)
        self.assertEqual(result["matches"], 8)
        self.assertIn("brier_score", result)
        self.assertIn("calibration", result)


if __name__ == "__main__":
    unittest.main()
