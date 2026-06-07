"""Pinnacle guest-API odds source.

Pinnacle is the sharpest reference book and — unlike AiScore, which only serves
a frozen pre-match total — publishes genuinely live in-play totals that move
with the game. It's reachable for free via an undocumented public guest key
(no account). SignalBook uses Pinnacle for ODDS while AiScore stays the live
game-state/stats source; the two are joined by team-name matching.

Grey-hat, read-only, for non-commercial paper-trading research. The guest key /
endpoint shape are undocumented and could change — treat breakage as expected
maintenance, not a crisis.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from quicklin_soccer.models import OddsQuote, now_iso
from quicklin_soccer.sports import (
    SPORT_BASEBALL,
    SPORT_BASKETBALL,
    SPORT_HOCKEY,
    SPORT_SOCCER,
    SPORT_TENNIS,
    sport_config,
)

PINNACLE_BASE = "https://guest.api.arcadia.pinnacle.com/0.1"
# Public guest key shipped in Pinnacle's own web client. Read-only odds access.
PINNACLE_GUEST_KEY = "CmX2KcMrXuFmNg6YFbmTxE0y9CIrOi0R"

# Our sport keys -> Pinnacle sport names.
_PINNACLE_SPORT_NAMES = {
    SPORT_SOCCER: "Soccer",
    SPORT_BASEBALL: "Baseball",
    SPORT_BASKETBALL: "Basketball",
    SPORT_HOCKEY: "Hockey",
    SPORT_TENNIS: "Tennis",
}


class PinnacleError(Exception):
    """Network/parse failure talking to the Pinnacle guest API."""


@dataclass(frozen=True)
class PinnacleGame:
    matchup_id: int
    sport: str
    league: str
    home: str
    away: str
    starts: str
    is_live: bool


@dataclass(frozen=True)
class PinnacleTotal:
    line: float
    over_decimal: float
    under_decimal: float


def american_to_decimal(american: Any) -> float | None:
    """Convert American odds to decimal. -108 -> 1.926, +384 -> 4.84."""
    try:
        value = float(american)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    if value > 0:
        return round(1 + value / 100.0, 4)
    return round(1 + 100.0 / abs(value), 4)


def _normalize_team(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def teams_match(a: str, b: str) -> bool:
    """Loose match between a Pinnacle and an AiScore team name. Handles exact,
    substring ("NY Mets" vs "New York Mets"), and a shared distinctive token
    (e.g. "Padres") so the two feeds' naming differences still join."""
    na, nb = _normalize_team(a), _normalize_team(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    tokens_a = {t for t in re.findall(r"[a-z0-9]+", (a or "").lower()) if len(t) >= 4}
    tokens_b = {t for t in re.findall(r"[a-z0-9]+", (b or "").lower()) if len(t) >= 4}
    return bool(tokens_a & tokens_b)


def find_game(home: str, away: str, games: list[PinnacleGame]) -> PinnacleGame | None:
    """Find the Pinnacle game matching an AiScore (home, away) pair. Requires
    both sides to match in the same alignment to avoid false positives."""
    for game in games:
        if teams_match(home, game.home) and teams_match(away, game.away):
            return game
    return None


def main_total_from_markets(markets: list[dict]) -> PinnacleTotal | None:
    """Extract the MAIN full-game over/under from a matchup's straight markets.
    The main line is the non-alternate total for period 0; alternates and
    sub-period totals are ignored. Returns None if it's absent or suspended."""
    for market in markets:
        if market.get("type") != "total" or market.get("period") != 0 or market.get("isAlternate"):
            continue
        prices = {p.get("designation"): p for p in market.get("prices", [])}
        over, under = prices.get("over"), prices.get("under")
        if not over or not under:
            return None
        over_decimal = american_to_decimal(over.get("price"))
        under_decimal = american_to_decimal(under.get("price"))
        line = over.get("points")
        if over_decimal is None or under_decimal is None or line is None:
            return None
        return PinnacleTotal(line=float(line), over_decimal=over_decimal, under_decimal=under_decimal)
    return None


def to_odds_quotes(total: PinnacleTotal, snapshot: Any, sport: str) -> tuple[OddsQuote, OddsQuote]:
    """Wrap a Pinnacle main total into the two OddsQuotes the strategy consumes."""
    base = dict(
        provider="pinnacle",
        provider_match_id=snapshot.provider_match_id,
        canonical_id=snapshot.canonical_id,
        captured_at=snapshot.captured_at or now_iso(),
        market=sport_config(sport).total_market,
        period="full_time",
        line=total.line,
        bookmaker="pinnacle",
        sport=sport,
        raw={"source": "pinnacle_guest", "line": total.line},
    )
    over = OddsQuote(**base, side="over", decimal_odds=total.over_decimal)
    under = OddsQuote(**base, side="under", decimal_odds=total.under_decimal)
    return over, under


def _default_fetch(path: str) -> Any:
    request = urllib.request.Request(
        f"{PINNACLE_BASE}{path}",
        headers={"User-Agent": "Mozilla/5.0", "X-API-Key": PINNACLE_GUEST_KEY},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        raise PinnacleError(f"{path}: {exc}") from exc


class PinnacleClient:
    """Thin client over the guest API. Inject `fetch` (path -> parsed JSON) to
    test against fixtures without the network."""

    def __init__(self, fetch: Callable[[str], Any] = _default_fetch):
        self._fetch = fetch
        self._sport_ids: dict[str, int] | None = None

    def _sports(self) -> dict[str, int]:
        if self._sport_ids is None:
            self._sport_ids = {s["name"]: s["id"] for s in self._fetch("/sports")}
        return self._sport_ids

    def sport_id(self, sport: str) -> int | None:
        name = _PINNACLE_SPORT_NAMES.get(sport)
        return self._sports().get(name) if name else None

    def live_games(self, sport: str) -> list[PinnacleGame]:
        sport_id = self.sport_id(sport)
        if sport_id is None:
            return []
        games: list[PinnacleGame] = []
        for matchup in self._fetch(f"/sports/{sport_id}/matchups"):
            if not matchup.get("isLive") or matchup.get("type") != "matchup":
                continue
            alignment = {p.get("alignment"): p.get("name") for p in matchup.get("participants", [])}
            home, away = alignment.get("home"), alignment.get("away")
            if not home or not away:
                continue
            games.append(
                PinnacleGame(
                    matchup_id=matchup["id"],
                    sport=sport,
                    league=(matchup.get("league") or {}).get("name", ""),
                    home=home,
                    away=away,
                    starts=matchup.get("startTime", ""),
                    is_live=True,
                )
            )
        return games

    def main_total(self, matchup_id: int) -> PinnacleTotal | None:
        return main_total_from_markets(self._fetch(f"/matchups/{matchup_id}/markets/related/straight"))
