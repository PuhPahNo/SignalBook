from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class MatchStats:
    url: str
    home_team: str
    away_team: str
    minute: int
    home_score: int
    away_score: int
    home_attacks: int
    away_attacks: int
    home_dangerous_attacks: int
    away_dangerous_attacks: int
    home_shots_on_target: int
    away_shots_on_target: int
    home_shots_off_target: int
    away_shots_off_target: int
    home_corners: int
    away_corners: int
    live_total_line: float | None = None
    live_over_odds: float | None = None
    live_under_odds: float | None = None
    provider_match_id: str | None = None
    league: str | None = None
    captured_at: str | None = None
    home_red_cards: int = 0
    away_red_cards: int = 0
    sport: str = "soccer"
    phase: str | None = None
    clock: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return f"{self.home_team} vs {self.away_team}"

    @property
    def total_score(self) -> int:
        return self.home_score + self.away_score

    @property
    def total_attacks(self) -> int:
        return self.home_attacks + self.away_attacks

    @property
    def total_dangerous_attacks(self) -> int:
        return self.home_dangerous_attacks + self.away_dangerous_attacks

    @property
    def total_shots_on_target(self) -> int:
        return self.home_shots_on_target + self.away_shots_on_target

    @property
    def total_shots_off_target(self) -> int:
        return self.home_shots_off_target + self.away_shots_off_target

    @property
    def adjusted_total_shots(self) -> float:
        return (self.total_shots_on_target * 1.5) + self.total_shots_off_target

    @property
    def adjusted_shots_per_10(self) -> float:
        return (self.adjusted_total_shots / self.minute) * 10

    @property
    def total_corners(self) -> int:
        return self.home_corners + self.away_corners


@dataclass(frozen=True)
class HistoricalProfile:
    final_goal_totals: tuple[int, ...]
    halftime_goal_totals: tuple[int, ...]

    def final_over_percent(self, threshold: int) -> float:
        return percentage_at_or_above(self.final_goal_totals, threshold)

    def halftime_over_percent(self, threshold: int) -> float:
        return percentage_at_or_above(self.halftime_goal_totals, threshold)

    @property
    def final_over_percents(self) -> tuple[float, float, float, float]:
        return tuple(self.final_over_percent(n) for n in range(1, 5))

    @property
    def halftime_over_percents(self) -> tuple[float, float, float, float]:
        return tuple(self.halftime_over_percent(n) for n in range(1, 5))


@dataclass(frozen=True)
class BetSignal:
    match: MatchStats
    pick: str
    model_total_line: float
    history: HistoricalProfile
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_row(self) -> dict[str, Any]:
        return {
            "match": self.match.title,
            "pick": self.pick,
            "model_line": self.model_total_line,
            "minute": self.match.minute,
            "score": f"{self.match.home_score}-{self.match.away_score}",
            "adjusted_shots_per_10": round(self.match.adjusted_shots_per_10, 2),
            "shots_on_target": self.match.total_shots_on_target,
            "shots_off_target": self.match.total_shots_off_target,
            "attacks": self.match.total_attacks,
            "dangerous_attacks": self.match.total_dangerous_attacks,
            "corners": self.match.total_corners,
            "live_total_line": self.match.live_total_line,
            "live_over_odds": self.match.live_over_odds,
            "live_under_odds": self.match.live_under_odds,
            "final_over_percents": self.history.final_over_percents,
            "halftime_over_percents": self.history.halftime_over_percents,
            "url": self.match.url,
        }


@dataclass(frozen=True)
class MatchRef:
    provider: str
    provider_match_id: str
    url: str
    home_team: str
    away_team: str
    league: str | None = None
    start_time: str | None = None
    sport: str = "soccer"

    @property
    def canonical_id(self) -> str:
        return canonical_match_id(self.home_team, self.away_team, self.start_time, self.sport)


@dataclass(frozen=True)
class LiveSnapshot:
    provider: str
    provider_match_id: str
    canonical_id: str
    url: str
    captured_at: str
    home_team: str
    away_team: str
    league: str | None
    minute: int
    status: str
    home_score: int
    away_score: int
    home_attacks: int
    away_attacks: int
    home_dangerous_attacks: int
    away_dangerous_attacks: int
    home_shots_on_target: int
    away_shots_on_target: int
    home_shots_off_target: int
    away_shots_off_target: int
    home_corners: int
    away_corners: int
    home_red_cards: int = 0
    away_red_cards: int = 0
    sport: str = "soccer"
    phase: str | None = None
    clock: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return f"{self.home_team} vs {self.away_team}"

    @property
    def total_score(self) -> int:
        return self.home_score + self.away_score

    @property
    def total_attacks(self) -> int:
        return self.home_attacks + self.away_attacks

    @property
    def total_dangerous_attacks(self) -> int:
        return self.home_dangerous_attacks + self.away_dangerous_attacks

    @property
    def total_shots_on_target(self) -> int:
        return self.home_shots_on_target + self.away_shots_on_target

    @property
    def total_shots_off_target(self) -> int:
        return self.home_shots_off_target + self.away_shots_off_target

    @property
    def adjusted_total_shots(self) -> float:
        return (self.total_shots_on_target * 1.5) + self.total_shots_off_target

    @property
    def adjusted_shots_per_10(self) -> float:
        return (self.adjusted_total_shots / max(self.minute, 1)) * 10

    @property
    def total_corners(self) -> int:
        return self.home_corners + self.away_corners


# A real two-sided over/under book's implied probabilities sum to >= 1 — the
# excess is the bookmaker's margin (overround). A pair summing below this is
# incoherent: it cannot be a genuine market, so it's a parse artifact. The
# listing-row parser used to produce such pairs (implied sum ~0.70), which
# manufactured large phantom EV. We allow a hair under 1.0 for decimal-odds
# rounding. See is_coherent_two_sided_odds.
MIN_TWO_SIDED_IMPLIED: float = 0.98


def is_coherent_two_sided_odds(over_odds: float | None, under_odds: float | None) -> bool:
    """True if (over, under) could be a real two-sided total. Both must be
    decimal odds > 1, and their implied probabilities must sum to ~>= 1."""
    if over_odds is None or under_odds is None:
        return False
    if over_odds <= 1.0 or under_odds <= 1.0:
        return False
    return (1.0 / over_odds) + (1.0 / under_odds) >= MIN_TWO_SIDED_IMPLIED


@dataclass(frozen=True)
class OddsQuote:
    provider: str
    provider_match_id: str
    canonical_id: str
    captured_at: str
    market: str
    period: str
    line: float
    side: str
    decimal_odds: float
    bookmaker: str = "aiscore"
    is_suspended: bool = False
    is_blocked: bool = False
    sport: str = "soccer"
    raw: dict[str, Any] = field(default_factory=dict)
    # Estimator-related fields. is_estimated=True means decimal_odds was
    # synthesized by OddsEstimator because the bookmaker didn't post the
    # two-sided total. estimation_method names the model used; confidence
    # is in [0,1] with 0.50 the live-bot's emit floor.
    is_estimated: bool = False
    estimation_method: str | None = None
    estimation_confidence: float | None = None


@dataclass(frozen=True)
class StrategyPrediction:
    strategy_version: str
    created_at: str
    provider_match_id: str
    canonical_id: str
    fair_probability: float
    fair_odds: float | None
    expected_goals_remaining: float
    confidence: float
    feature_values: dict[str, Any]
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    sport: str = "soccer"

    @property
    def expected_total_remaining(self) -> float:
        return self.expected_goals_remaining


@dataclass(frozen=True)
class ValueSignal:
    strategy_version: str
    created_at: str
    provider_match_id: str
    canonical_id: str
    match_title: str
    url: str
    side: str
    line: float
    offered_odds: float
    fair_probability: float
    fair_odds: float | None
    expected_value: float
    stake_units: float
    expected_goals_remaining: float
    confidence: float
    minute: int
    score: str
    bookmaker: str
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    sport: str = "soccer"
    # When odds_source != 'market', the offered_odds came from OddsEstimator
    # rather than a bookmaker quote. odds_confidence is the estimator's
    # confidence in [0,1]; it's stored on the signal so the dashboard and
    # post-hoc analytics can split estimated vs market-priced PnL.
    odds_source: str = "market"
    odds_confidence: float | None = None

    @property
    def expected_total_remaining(self) -> float:
        return self.expected_goals_remaining

    @property
    def is_estimated(self) -> bool:
        return self.odds_source != "market"

    def as_row(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "match": self.match_title,
            "side": self.side,
            "line": self.line,
            "offered_odds": self.offered_odds,
            "fair_probability": round(self.fair_probability, 4),
            "fair_odds": round(self.fair_odds, 3) if self.fair_odds else None,
            "ev": round(self.expected_value, 4),
            "stake_units": self.stake_units,
            "minute": self.minute,
            "score": self.score,
            "expected_goals_remaining": round(self.expected_goals_remaining, 3),
            "expected_total_remaining": round(self.expected_total_remaining, 3),
            "confidence": round(self.confidence, 3),
            "bookmaker": self.bookmaker,
            "strategy_version": self.strategy_version,
            "reasons": ",".join(self.reason_codes),
            "url": self.url,
            "odds_source": self.odds_source,
            "odds_confidence": round(self.odds_confidence, 3) if self.odds_confidence is not None else None,
        }


@dataclass(frozen=True)
class Settlement:
    signal_id: int
    match_id: int
    settled_at: str
    final_home_score: int
    final_away_score: int
    result: str
    payout_units: float


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    checked_at: str
    ok: bool
    message: str
    latency_ms: int | None = None
    sport: str = "soccer"


def percentage_at_or_above(values: tuple[int, ...], threshold: int) -> float:
    if not values:
        return 0.0
    hits = sum(1 for value in values if value >= threshold)
    return round((hits / len(values)) * 100, 2)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical_match_id(home_team: str, away_team: str, start_time: str | None = None, sport: str = "soccer") -> str:
    home = _slug(home_team)
    away = _slug(away_team)
    date_part = (start_time or "unknown").split("T", 1)[0]
    base = f"{date_part}:{home}:{away}"
    return base if sport == "soccer" else f"{sport}:{base}"


def _slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    return "-".join(part for part in cleaned.split("-") if part)
