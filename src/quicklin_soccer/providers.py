from __future__ import annotations

from typing import Protocol

from quicklin_soccer.aiscore import AiScoreScraper, BrowserConfig
from quicklin_soccer.models import LiveSnapshot, MatchRef, OddsQuote, canonical_match_id, now_iso
from quicklin_soccer.sports import SPORT_SOCCER, sport_config


class LiveDataProvider(Protocol):
    name: str

    def list_live_matches(self, sport: str = SPORT_SOCCER, limit: int | None = None) -> list[MatchRef]:
        ...

    def get_live_snapshot(self, match_ref: MatchRef) -> tuple[LiveSnapshot, tuple[OddsQuote, ...]]:
        ...

    def get_odds(self, match_ref: MatchRef) -> tuple[OddsQuote, ...]:
        ...

    def get_historical_matches(self, match_ref: MatchRef):
        ...

    def settle_match(self, match_ref: MatchRef) -> dict:
        ...


class AiScoreProvider:
    name = "aiscore"

    def __init__(self, scraper: AiScoreScraper):
        self.scraper = scraper

    @classmethod
    def create(cls, config: BrowserConfig | None = None) -> "AiScoreProviderContext":
        return AiScoreProviderContext(config or BrowserConfig())

    def list_live_matches(self, sport: str = SPORT_SOCCER, limit: int | None = None) -> list[MatchRef]:
        refs: list[MatchRef] = []
        for url in self.scraper.live_match_urls(limit=limit, sport=sport):
            try:
                refs.append(self.scraper.match_ref(url, sport=sport))
            except Exception:
                continue
        return refs

    def get_live_snapshot(self, match_ref: MatchRef) -> tuple[LiveSnapshot, tuple[OddsQuote, ...]]:
        stats = self.scraper.match_stats(match_ref.url, sport=match_ref.sport)
        captured_at = stats.captured_at or now_iso()
        canonical_id = match_ref.canonical_id or canonical_match_id(stats.home_team, stats.away_team, sport=match_ref.sport)
        snapshot = LiveSnapshot(
            provider=self.name,
            provider_match_id=stats.provider_match_id or match_ref.provider_match_id,
            canonical_id=canonical_id,
            url=stats.url,
            captured_at=captured_at,
            home_team=stats.home_team,
            away_team=stats.away_team,
            league=stats.league or match_ref.league,
            minute=stats.minute,
            status="live",
            home_score=stats.home_score,
            away_score=stats.away_score,
            home_attacks=stats.home_attacks,
            away_attacks=stats.away_attacks,
            home_dangerous_attacks=stats.home_dangerous_attacks,
            away_dangerous_attacks=stats.away_dangerous_attacks,
            home_shots_on_target=stats.home_shots_on_target,
            away_shots_on_target=stats.away_shots_on_target,
            home_shots_off_target=stats.home_shots_off_target,
            away_shots_off_target=stats.away_shots_off_target,
            home_corners=stats.home_corners,
            away_corners=stats.away_corners,
            home_red_cards=stats.home_red_cards,
            away_red_cards=stats.away_red_cards,
            sport=match_ref.sport,
            phase=stats.phase,
            clock=stats.clock,
            stats=stats.stats,
            raw={"source": "aiscore_nuxt", "sport": match_ref.sport},
        )
        return snapshot, self._odds_quotes(snapshot, stats.live_total_line, stats.live_over_odds, stats.live_under_odds)

    def get_odds(self, match_ref: MatchRef) -> tuple[OddsQuote, ...]:
        snapshot, odds = self.get_live_snapshot(match_ref)
        return odds

    def historical_profile(self, match_ref: MatchRef):
        return self.scraper.historical_profile(match_ref.url)

    def get_historical_matches(self, match_ref: MatchRef):
        return self.historical_profile(match_ref)

    def settle_match(self, match_ref: MatchRef) -> dict:
        return self.scraper.match_result(
            match_ref.url,
            sport=match_ref.sport,
            home_team=match_ref.home_team,
            away_team=match_ref.away_team,
        )

    def _odds_quotes(
        self,
        snapshot: LiveSnapshot,
        line: float | None,
        over_odds: float | None,
        under_odds: float | None,
    ) -> tuple[OddsQuote, ...]:
        if line is None or over_odds is None or under_odds is None:
            return ()
        base = {
            "provider": self.name,
            "provider_match_id": snapshot.provider_match_id,
            "canonical_id": snapshot.canonical_id,
            "captured_at": snapshot.captured_at,
            "market": sport_config(snapshot.sport).total_market,
            "period": "full_time",
            "line": line,
            "bookmaker": "aiscore",
            "sport": snapshot.sport,
            "raw": {"source": "aiscore_nuxt", "sport": snapshot.sport},
        }
        return (
            OddsQuote(**base, side="over", decimal_odds=over_odds),
            OddsQuote(**base, side="under", decimal_odds=under_odds),
        )


class AiScoreProviderContext:
    def __init__(self, config: BrowserConfig):
        self.scraper = AiScoreScraper(config)
        self.provider = AiScoreProvider(self.scraper)

    def __enter__(self) -> AiScoreProvider:
        return self.provider

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.scraper.close()
