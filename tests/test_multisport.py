from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.aiscore import _is_final_state, _is_live_match_link, _parse_detail_scoreboard, _parse_listing_match
from quicklin_soccer.models import LiveSnapshot, MatchRef, OddsQuote, ValueSignal, canonical_match_id, now_iso
from quicklin_soccer.storage import QuicklinStore
from quicklin_soccer.strategy import StrategyConfig, evaluate_sport_totals


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
        "minute": 30,
        "status": "live",
        "home_score": 2,
        "away_score": 1,
        "home_attacks": 0,
        "away_attacks": 0,
        "home_dangerous_attacks": 0,
        "away_dangerous_attacks": 0,
        "home_shots_on_target": 18,
        "away_shots_on_target": 15,
        "home_shots_off_target": 0,
        "away_shots_off_target": 0,
        "home_corners": 0,
        "away_corners": 0,
        "sport": sport,
        "phase": "P2",
        "clock": "10:00",
        "stats": {"shots_on_goal": 33},
    }
    data.update(overrides)
    return LiveSnapshot(**data)


def quote(sport: str, side: str, odds: float, line: float = 5.5) -> OddsQuote:
    markets = {
        "hockey": "total_goals",
        "basketball": "total_points",
        "baseball": "total_runs",
        "tennis": "total_games",
    }
    return OddsQuote(
        provider="test",
        provider_match_id=f"{sport}-1",
        canonical_id="cid",
        captured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        market=markets[sport],
        period="full_time",
        line=line,
        side=side,
        decimal_odds=odds,
        sport=sport,
    )


class MultiSportTests(unittest.TestCase):
    def test_soccer_canonical_id_stays_backward_compatible(self):
        self.assertEqual(
            canonical_match_id("Home", "Away", "2026-05-23T12:00:00+00:00"),
            "2026-05-23:home:away",
        )
        self.assertEqual(
            MatchRef("test", "1", "https://example.test", "Home", "Away", sport="hockey").canonical_id,
            "hockey:unknown:home:away",
        )

    def test_hockey_requires_two_sided_total_odds(self):
        result = evaluate_sport_totals(snapshot("hockey"), (quote("hockey", "over", 2.1),))
        self.assertEqual(result.skip_reason, "missing_two_sided_total_odds")

    def test_multisport_live_link_detection_matches_current_aiscore_markup(self):
        self.assertTrue(
            _is_live_match_link(
                "https://www.aiscore.com/ice-hockey/match-slovakia-czech-republic/vrqwwb02x92h4qn",
                "10:20 AM\nP3-09:03\nSlovakia\nCzech Republic\n2\n1",
                "hockey",
            )
        )
        self.assertTrue(
            _is_live_match_link(
                "https://www.aiscore.com/basketball/match-kapfenberg-bulls-oberwart-gunners/jr7o9s39ov0ig70",
                "12:00 PM\nQ2\n-\n05:27\nKapfenberg Bulls\nOberwart Gunners",
                "basketball",
            )
        )
        self.assertTrue(
            _is_live_match_link(
                "https://www.aiscore.com/baseball/match-ostrava-sabat-prague/6975vcxjvygagq2",
                "9:00 AM\n5th Inning\nOstrava\nSaBaT Prague",
                "baseball",
            )
        )
        self.assertTrue(
            _is_live_match_link(
                "https://www.aiscore.com/tennis/match-max-alcala-gurri-buvaysar-gadamauri/9gkloujjll8fmkx",
                "11:00 AM\nS3\nMax Alcala Gurri\nBuvaysar Gadamauri",
                "tennis",
            )
        )
        self.assertFalse(
            _is_live_match_link(
                "https://www.aiscore.com/ice-hockey/match-carolina-hurricanes-montreal-canadiens/xvkjpb1z1d8i8k9/h2h",
                "7:00 PM\n-\nCarolina Hurricanes\nMontreal Canadiens",
                "hockey",
            )
        )

    def test_multisport_listing_text_parses_state_score_and_totals(self):
        basketball = _parse_listing_match(
            "12:00 PM\nQ2\n-\n05:27\nKapfenberg Bulls\nOberwart Gunners\n25\n16\n9\n27\n18\n9\n"
            "1.43\n2.65\n- 4.5\n+4.5\n1.83\n1.83\nO 153.5\nU 153.5\n1.86\n1.80",
            "basketball",
        )
        self.assertEqual(basketball["home_team"], "Kapfenberg Bulls")
        self.assertEqual(basketball["away_team"], "Oberwart Gunners")
        self.assertEqual(basketball["home_score"], 25)
        self.assertEqual(basketball["away_score"], 27)
        self.assertEqual(basketball["phase"], "Q2")
        self.assertEqual(basketball["clock"], "05:27")
        self.assertEqual(basketball["odds"], (153.5, 1.86, 1.8))

        baseball = _parse_listing_match("9:00 AM\n5th Inning\nOstrava\nSaBaT Prague\n4\n3\n-\n-\n-", "baseball")
        self.assertEqual(baseball["phase"], "inning_5")
        self.assertEqual(baseball["home_score"], 4)
        self.assertEqual(baseball["away_score"], 3)

    def test_live_non_soccer_page_text_does_not_look_final_from_boilerplate(self):
        text = (
            "Army Basket Club\n70\nQ4 04:23\n35\nAbidjan Basket Club\n"
            "AiScore provides quarter results, halftime or final result."
        )
        self.assertFalse(_is_final_state(text, "basketball", None))
        self.assertEqual(_parse_detail_scoreboard(text, "Army Basket Club", "Abidjan Basket Club", "basketball"), (70, 35))

    def test_basketball_high_total_prices_without_overflow(self):
        result = evaluate_sport_totals(
            snapshot("basketball", phase="Q3", clock="06:00", minute=30, home_score=72, away_score=70),
            (quote("basketball", "over", 1.91, 224.5), quote("basketball", "under", 1.91, 224.5)),
            StrategyConfig(strategy_version="basketball_totals_v1"),
        )
        self.assertIsNotNone(result.prediction)
        self.assertEqual(result.prediction.sport, "basketball")

    def test_storage_summary_filters_by_sport(self):
        with tempfile.TemporaryDirectory() as directory:
            with QuicklinStore(Path(directory) / "quicklin.db") as store:
                soccer = MatchRef("test", "s1", "https://example.test/s", "A", "B")
                hockey = MatchRef("test", "h1", "https://example.test/h", "C", "D", sport="hockey")
                store.upsert_match(soccer)
                store.upsert_match(hockey)
                self.assertEqual(store.report_summary("soccer")["matches"], 1)
                self.assertEqual(store.report_summary("hockey")["matches"], 1)

    def test_storage_finds_duplicate_open_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            with QuicklinStore(Path(directory) / "quicklin.db") as store:
                ref = MatchRef("test", "h1", "https://example.test/h", "C", "D", sport="hockey")
                match_id = store.upsert_match(ref)
                signal = ValueSignal(
                    strategy_version="hockey_totals_v1",
                    created_at=now_iso(),
                    provider_match_id=ref.provider_match_id,
                    canonical_id=ref.canonical_id,
                    match_title="C vs D",
                    url=ref.url,
                    side="over",
                    line=5.5,
                    offered_odds=1.91,
                    fair_probability=0.55,
                    fair_odds=1.82,
                    expected_value=0.05,
                    stake_units=1.0,
                    expected_goals_remaining=2.1,
                    confidence=0.7,
                    minute=35,
                    score="2-1",
                    bookmaker="test",
                    sport="hockey",
                )
                signal_id = store.insert_signal(match_id, None, None, None, signal)
                self.assertEqual(store.open_signal_id(match_id, signal), signal_id)
                self.assertEqual(store.emitted_signal_id(match_id, signal), signal_id)


if __name__ == "__main__":
    unittest.main()
