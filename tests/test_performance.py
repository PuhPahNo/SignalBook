"""Performance view: hit rate vs base rate (the skill benchmark).

A high hit rate means nothing on its own — the model could just be riding the
base rate (e.g. always 'under' on high lines). edge = hit_rate - base_rate is
the honest measure. base_rate is the best always-one-side strategy.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.models import MatchRef, Settlement, ValueSignal, now_iso
from quicklin_soccer.storage import QuicklinStore
from quicklin_soccer.web.read_models import _sport_params, performance_query


def _signal(side: str, line: float) -> ValueSignal:
    return ValueSignal(
        strategy_version="baseball_totals_v1",
        created_at=now_iso(),
        provider_match_id="p",
        canonical_id="c",
        match_title="A vs B",
        url="https://example.test",
        side=side,
        line=line,
        offered_odds=2.0,
        fair_probability=0.6,
        fair_odds=1.67,
        expected_value=0.0,
        stake_units=1.0,
        expected_goals_remaining=2.0,
        confidence=0.6,
        minute=4,
        score="1-1",
        bookmaker="aiscore_open",
        sport="baseball",
        odds_source="prediction_aiscore_open",
    )


class PerformanceBaseRateTests(unittest.TestCase):
    def test_edge_is_hit_rate_minus_best_one_side_strategy(self):
        # 4 games, line 8.5, finals 5/6/12/13 -> base split 2 under / 2 over,
        # so base_rate = 0.5. The model called each correctly -> hit 1.0,
        # edge 0.5.
        cases = [("under", 5), ("under", 6), ("over", 12), ("over", 13)]
        with tempfile.TemporaryDirectory() as directory:
            with QuicklinStore(Path(directory) / "q.db") as store:
                for i, (side, final) in enumerate(cases):
                    ref = MatchRef("test", f"m{i}", f"https://example.test/{i}", "A", "B", sport="baseball")
                    match_id = store.upsert_match(ref)
                    sig_id = store.insert_signal(match_id, None, None, None, _signal(side, 8.5))
                    won = (side == "under" and final < 8.5) or (side == "over" and final > 8.5)
                    store.insert_settlement(
                        Settlement(
                            signal_id=sig_id,
                            match_id=match_id,
                            settled_at=now_iso(),
                            final_home_score=final,
                            final_away_score=0,
                            result="win" if won else "loss",
                            payout_units=1.0 if won else -1.0,
                        ),
                        None,
                    )
                rows = [dict(r) for r in store.conn.execute(performance_query("baseball"), _sport_params("baseball"))]

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["settled"], 4)
        self.assertEqual(row["hit_rate"], 1.0)
        self.assertEqual(row["base_rate"], 0.5)  # 2 over, 2 under -> best one-side = 0.5
        self.assertEqual(row["edge"], 0.5)
        self.assertEqual(row["over_picks"], 2)
        self.assertEqual(row["under_picks"], 2)
        self.assertEqual(row["line_source"], "prediction_aiscore_open")

    def test_riding_base_rate_shows_zero_edge(self):
        # Always 'under' on a high line where every game stays under: hit 1.0,
        # but base_rate is also 1.0 -> edge 0. No skill demonstrated.
        with tempfile.TemporaryDirectory() as directory:
            with QuicklinStore(Path(directory) / "q.db") as store:
                for i, final in enumerate([3, 4, 5]):
                    ref = MatchRef("test", f"m{i}", f"https://example.test/{i}", "A", "B", sport="baseball")
                    match_id = store.upsert_match(ref)
                    sig_id = store.insert_signal(match_id, None, None, None, _signal("under", 9.5))
                    store.insert_settlement(
                        Settlement(sig_id, match_id, now_iso(), final, 0, "win", 1.0), None
                    )
                rows = [dict(r) for r in store.conn.execute(performance_query("baseball"), _sport_params("baseball"))]

        row = rows[0]
        self.assertEqual(row["hit_rate"], 1.0)
        self.assertEqual(row["base_rate"], 1.0)
        self.assertEqual(row["edge"], 0.0)


if __name__ == "__main__":
    unittest.main()
