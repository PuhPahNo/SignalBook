from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.cli import (
    _capture_page_text,
    _is_settlement_ready,
    _settlement_skip_reason,
)
from quicklin_soccer.models import MatchRef


def result(**overrides) -> dict:
    base = {
        "home_score": 3,
        "away_score": 2,
        "status_id": None,
        "minute": 0,
        "is_final": False,
        "score_source": "detail_scoreboard",
    }
    base.update(overrides)
    return base


class SettlementSkipReasonTests(unittest.TestCase):
    def test_non_soccer_without_detail_scoreboard_is_the_tennis_baseball_bug(self):
        # The exact reason tennis & baseball pile up: no detail scoreboard.
        for sport in ("tennis", "baseball"):
            with self.subTest(sport=sport):
                reason = _settlement_skip_reason(sport, result(score_source="nuxt_match"))
                self.assertEqual(reason, "no_detail_scoreboard")

    def test_non_soccer_final_with_detail_scoreboard_is_ready(self):
        for sport in ("basketball", "hockey", "baseball", "tennis"):
            with self.subTest(sport=sport):
                reason = _settlement_skip_reason(sport, result(is_final=True))
                self.assertIsNone(reason)

    def test_non_soccer_final_status_id_is_ready(self):
        reason = _settlement_skip_reason("basketball", result(status_id=10))
        self.assertIsNone(reason)

    def test_non_soccer_detail_scoreboard_but_not_final(self):
        reason = _settlement_skip_reason("basketball", result(is_final=False))
        self.assertEqual(reason, "not_final")

    def test_soccer_under_90_minutes(self):
        self.assertEqual(_settlement_skip_reason("soccer", result(minute=70)), "soccer_under_90")

    def test_soccer_at_90_minutes_is_ready(self):
        self.assertIsNone(_settlement_skip_reason("soccer", result(minute=90)))

    def test_soccer_does_not_require_detail_scoreboard(self):
        # Soccer settles on minute>=90 even from a nuxt-only score.
        self.assertIsNone(_settlement_skip_reason("soccer", result(minute=95, score_source="nuxt_match")))

    def test_is_settlement_ready_matches_reason(self):
        ready = result(is_final=True)
        not_ready = result(score_source="nuxt_match")
        self.assertTrue(_is_settlement_ready("baseball", ready))
        self.assertFalse(_is_settlement_ready("baseball", not_ready))


class CapturePageTextTests(unittest.TestCase):
    def _ref(self, sport: str) -> MatchRef:
        return MatchRef(
            provider="aiscore",
            provider_match_id=f"https://www.aiscore.com/{sport}/match-a-b/xyz123",
            url=f"https://www.aiscore.com/{sport}/match-a-b/xyz123",
            home_team="A",
            away_team="B",
            sport=sport,
        )

    def test_writes_sanitized_file_with_page_text(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            wrote = _capture_page_text(
                capture_dir, self._ref("baseball"), result(page_text="9th Inning\nA 5\nB 3")
            )
            self.assertTrue(wrote)
            files = list(capture_dir.glob("*.txt"))
            self.assertEqual(len(files), 1)
            # Slashes/colons from the URL id must not create nested dirs.
            self.assertNotIn("/", files[0].name[:-4])
            self.assertIn("9th Inning", files[0].read_text(encoding="utf-8"))

    def test_no_page_text_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            capture_dir = Path(directory)
            wrote = _capture_page_text(capture_dir, self._ref("tennis"), result())
            self.assertFalse(wrote)
            self.assertEqual(list(capture_dir.glob("*.txt")), [])


if __name__ == "__main__":
    unittest.main()
