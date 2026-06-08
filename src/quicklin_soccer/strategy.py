from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from quicklin_soccer.models import (
    BetSignal,
    HistoricalProfile,
    LiveSnapshot,
    OddsQuote,
    StrategyPrediction,
    ValueSignal,
    now_iso,
)
from quicklin_soccer.models import MatchStats
from quicklin_soccer.odds_estimator import OddsEstimator
from quicklin_soccer.pricing import (
    expected_total_exposure,
    is_supported_total_line,
    no_vig_probabilities,
)
from quicklin_soccer.sports import (
    SPORT_BASEBALL,
    SPORT_BASKETBALL,
    SPORT_HOCKEY,
    SPORT_SOCCER,
    SPORT_TENNIS,
    sport_config,
)


PACE_OVER_THRESHOLD = 2.5
PACE_UNDER_THRESHOLD = 1.5
LEGACY_STRATEGY_VERSION = "legacy_threshold_v1"
DEFAULT_STRATEGY_VERSION = "ev_totals_v1"
HOCKEY_STRATEGY_VERSION = "hockey_totals_v1"
BASKETBALL_STRATEGY_VERSION = "basketball_totals_v1"
BASEBALL_STRATEGY_VERSION = "baseball_totals_v1"
TENNIS_STRATEGY_VERSION = "tennis_match_totals_v1"
SPORT_STRATEGY_VERSIONS = (
    DEFAULT_STRATEGY_VERSION,
    LEGACY_STRATEGY_VERSION,
    HOCKEY_STRATEGY_VERSION,
    BASKETBALL_STRATEGY_VERSION,
    BASEBALL_STRATEGY_VERSION,
    TENNIS_STRATEGY_VERSION,
)

FT_OVER_THRESHOLDS = {
    0.5: (1, 85.0),
    1.5: (2, 75.0),
    2.5: (3, 55.0),
    3.5: (4, 40.0),
}

FT_UNDER_THRESHOLDS = {
    0.5: (1, 70.0),
    1.5: (2, 60.0),
    2.5: (3, 50.0),
    3.5: (4, 40.0),
}

HT_OVER_THRESHOLDS = {
    0.5: (1, 60.0),
    1.5: (2, 55.0),
    2.5: (3, 55.0),
    3.5: (4, 55.0),
}

HT_UNDER_THRESHOLDS = {
    0.5: (1, 55.0),
    1.5: (2, 55.0),
    2.5: (3, 55.0),
    3.5: (4, 55.0),
}


@dataclass(frozen=True)
class StrategyConfig:
    strategy_version: str = DEFAULT_STRATEGY_VERSION
    min_ev: float = 0.03
    max_odds_age_seconds: int = 120
    stake_units: float = 1.0
    automatic_betting: bool = False
    calibration_metrics: dict | None = None
    calibration_status: str | None = None


@dataclass(frozen=True)
class EvEvaluation:
    prediction: StrategyPrediction | None
    signals: tuple[ValueSignal, ...]
    skip_reason: str | None = None
    details: dict | None = None


def needs_history(stats: MatchStats) -> bool:
    if not (1 <= stats.minute <= 80):
        return False
    if stats.total_score > 3:
        return False
    pace = stats.adjusted_shots_per_10
    return pace >= PACE_OVER_THRESHOLD or pace <= PACE_UNDER_THRESHOLD


def evaluate_match(stats: MatchStats, history: HistoricalProfile) -> BetSignal | None:
    if not needs_history(stats):
        return None
    if not history.final_goal_totals or not history.halftime_goal_totals:
        return None

    model_line = stats.total_score + 0.5
    pace = stats.adjusted_shots_per_10
    reasons = [
        f"adjusted shots/10={pace:.2f}",
        f"score={stats.home_score}-{stats.away_score}",
        f"minute={stats.minute}",
    ]

    if stats.minute >= 45:
        if pace >= PACE_OVER_THRESHOLD and _meets_over(history, model_line, FT_OVER_THRESHOLDS):
            return BetSignal(stats, "Full Time Over", model_line, history, tuple(reasons))
        if pace <= PACE_UNDER_THRESHOLD and _meets_under(history, model_line, FT_UNDER_THRESHOLDS):
            return BetSignal(stats, "Full Time Under", model_line, history, tuple(reasons))

    if stats.minute < 40:
        if pace >= PACE_OVER_THRESHOLD and _meets_halftime_over(history, model_line):
            return BetSignal(stats, "First Half Over", model_line, history, tuple(reasons))
        if pace <= PACE_UNDER_THRESHOLD and _meets_halftime_under(history, model_line):
            return BetSignal(stats, "First Half Under", model_line, history, tuple(reasons))

    return None


def evaluate_ev_totals(
    snapshot: LiveSnapshot,
    odds_quotes: tuple[OddsQuote, ...],
    history: HistoricalProfile | None = None,
    config: StrategyConfig | None = None,
) -> EvEvaluation:
    config = config or StrategyConfig()
    if not (1 <= snapshot.minute <= 88):
        return EvEvaluation(None, (), "outside_minute_window", {"minute": snapshot.minute})

    quote_by_side = _total_quotes_by_side(odds_quotes, "total_goals")
    over_quote = quote_by_side.get("over")
    under_quote = quote_by_side.get("under")
    if over_quote is None or under_quote is None:
        return EvEvaluation(None, (), "missing_two_sided_total_odds", {})
    if over_quote.line != under_quote.line:
        return EvEvaluation(None, (), "mismatched_total_lines", {"over": over_quote.line, "under": under_quote.line})
    if not is_supported_total_line(over_quote.line):
        return EvEvaluation(None, (), "unsupported_line", {"line": over_quote.line})
    if over_quote.is_suspended or over_quote.is_blocked or under_quote.is_suspended or under_quote.is_blocked:
        return EvEvaluation(None, (), "odds_suspended_or_blocked", {})
    odds_age = max(_age_seconds(over_quote.captured_at), _age_seconds(under_quote.captured_at))
    if odds_age > config.max_odds_age_seconds:
        return EvEvaluation(None, (), "stale_odds", {"age_seconds": odds_age})

    over_market_prob, under_market_prob = no_vig_probabilities(over_quote.decimal_odds, under_quote.decimal_odds)
    expected_goals_remaining = estimate_expected_goals_remaining(snapshot, over_quote.line, history)
    base_exposure = expected_total_exposure(
        current_total=snapshot.total_score,
        line=over_quote.line,
        side="over",
        expected_goals_remaining=expected_goals_remaining,
    )
    confidence = estimate_confidence(snapshot, history)
    prediction = StrategyPrediction(
        strategy_version=config.strategy_version,
        created_at=now_iso(),
        provider_match_id=snapshot.provider_match_id,
        canonical_id=snapshot.canonical_id,
        fair_probability=base_exposure.fair_probability,
        fair_odds=base_exposure.fair_odds,
        expected_goals_remaining=expected_goals_remaining,
        confidence=confidence,
        feature_values={
            "minute": snapshot.minute,
            "score": snapshot.total_score,
            "line": over_quote.line,
            "adjusted_shots_per_10": snapshot.adjusted_shots_per_10,
            "dangerous_attacks_per_10": _per_10(snapshot.total_dangerous_attacks, snapshot.minute),
            "market_over_probability": over_market_prob,
            "market_under_probability": under_market_prob,
            "odds_age_seconds": odds_age,
            "history_final_sample": len(history.final_goal_totals) if history else 0,
            "history_halftime_sample": len(history.halftime_goal_totals) if history else 0,
        },
        reason_codes=("stats_present", "two_sided_odds", "quarter_line_supported"),
        sport=snapshot.sport,
    )

    signals: list[ValueSignal] = []
    for quote in (over_quote, under_quote):
        exposure = expected_total_exposure(
            current_total=snapshot.total_score,
            line=quote.line,
            side=quote.side,
            expected_goals_remaining=expected_goals_remaining,
        )
        ev = exposure.expected_value(quote.decimal_odds)
        if ev < config.min_ev:
            continue
        signals.append(
            ValueSignal(
                strategy_version=config.strategy_version,
                created_at=now_iso(),
                provider_match_id=snapshot.provider_match_id,
                canonical_id=snapshot.canonical_id,
                match_title=snapshot.title,
                url=snapshot.url,
                side=quote.side,
                line=quote.line,
                offered_odds=quote.decimal_odds,
                fair_probability=exposure.fair_probability,
                fair_odds=exposure.fair_odds,
                expected_value=ev,
                stake_units=config.stake_units,
                expected_goals_remaining=expected_goals_remaining,
                confidence=confidence,
                minute=snapshot.minute,
                score=f"{snapshot.home_score}-{snapshot.away_score}",
                bookmaker=quote.bookmaker,
                reason_codes=("positive_ev", f"ev>={config.min_ev:.2%}"),
                sport=snapshot.sport,
            )
        )

    if not signals:
        return EvEvaluation(prediction, (), "no_edge", {"min_ev": config.min_ev})
    signals.sort(key=lambda signal: signal.expected_value, reverse=True)
    return EvEvaluation(prediction, tuple(signals), None, None)


def evaluate_sport_totals(
    snapshot: LiveSnapshot,
    odds_quotes: tuple[OddsQuote, ...],
    config: StrategyConfig | None = None,
) -> EvEvaluation:
    if snapshot.sport == SPORT_SOCCER:
        return evaluate_ev_totals(snapshot, odds_quotes, None, config)

    config = config or StrategyConfig(strategy_version=sport_config(snapshot.sport).default_strategy)
    market = sport_config(snapshot.sport).total_market
    gate_reason = _sport_state_gate(snapshot)
    if gate_reason:
        return EvEvaluation(None, (), gate_reason, {"sport": snapshot.sport, "phase": snapshot.phase, "clock": snapshot.clock})

    quote_by_side = _total_quotes_by_side(odds_quotes, market)
    over_quote = quote_by_side.get("over")
    under_quote = quote_by_side.get("under")
    if over_quote is None or under_quote is None:
        return EvEvaluation(None, (), "missing_two_sided_total_odds", {"sport": snapshot.sport, "market": market})
    if over_quote.line != under_quote.line:
        return EvEvaluation(None, (), "mismatched_total_lines", {"over": over_quote.line, "under": under_quote.line})
    if not is_supported_total_line(over_quote.line):
        return EvEvaluation(None, (), "unsupported_line", {"line": over_quote.line})
    if over_quote.is_suspended or over_quote.is_blocked or under_quote.is_suspended or under_quote.is_blocked:
        return EvEvaluation(None, (), "odds_suspended_or_blocked", {})
    odds_age = max(_age_seconds(over_quote.captured_at), _age_seconds(under_quote.captured_at))
    if odds_age > config.max_odds_age_seconds:
        return EvEvaluation(None, (), "stale_odds", {"age_seconds": odds_age})

    over_market_prob, under_market_prob = no_vig_probabilities(over_quote.decimal_odds, under_quote.decimal_odds)
    expected_remaining = estimate_expected_total_remaining(snapshot, over_quote.line, config.calibration_metrics)
    max_remaining = _max_remaining_units(snapshot.sport, expected_remaining)
    base_exposure = expected_total_exposure(
        current_total=snapshot.total_score,
        line=over_quote.line,
        side="over",
        expected_goals_remaining=expected_remaining,
        max_remaining_goals=max_remaining,
    )
    confidence = estimate_sport_confidence(snapshot, config.calibration_status)
    prediction = StrategyPrediction(
        strategy_version=config.strategy_version,
        created_at=now_iso(),
        provider_match_id=snapshot.provider_match_id,
        canonical_id=snapshot.canonical_id,
        fair_probability=base_exposure.fair_probability,
        fair_odds=base_exposure.fair_odds,
        expected_goals_remaining=expected_remaining,
        confidence=confidence,
        feature_values={
            "sport": snapshot.sport,
            "phase": snapshot.phase,
            "clock": snapshot.clock,
            "score": snapshot.total_score,
            "line": over_quote.line,
            "market": market,
            "market_over_probability": over_market_prob,
            "market_under_probability": under_market_prob,
            "odds_age_seconds": odds_age,
            "stats": snapshot.stats,
            "calibration_status": config.calibration_status,
            "calibration_metrics": config.calibration_metrics or {},
        },
        reason_codes=("state_present", "two_sided_odds", "quarter_line_supported", config.calibration_status or "uncalibrated"),
        sport=snapshot.sport,
    )

    signals: list[ValueSignal] = []
    for quote in (over_quote, under_quote):
        exposure = expected_total_exposure(
            current_total=snapshot.total_score,
            line=quote.line,
            side=quote.side,
            expected_goals_remaining=expected_remaining,
            max_remaining_goals=max_remaining,
        )
        ev = exposure.expected_value(quote.decimal_odds)
        if ev < config.min_ev:
            continue
        signals.append(
            ValueSignal(
                strategy_version=config.strategy_version,
                created_at=now_iso(),
                provider_match_id=snapshot.provider_match_id,
                canonical_id=snapshot.canonical_id,
                match_title=snapshot.title,
                url=snapshot.url,
                side=quote.side,
                line=quote.line,
                offered_odds=quote.decimal_odds,
                fair_probability=exposure.fair_probability,
                fair_odds=exposure.fair_odds,
                expected_value=ev,
                stake_units=config.stake_units,
                expected_goals_remaining=expected_remaining,
                confidence=confidence,
                minute=snapshot.minute,
                score=f"{snapshot.home_score}-{snapshot.away_score}",
                bookmaker=quote.bookmaker,
                reason_codes=("positive_ev", f"ev>={config.min_ev:.2%}"),
                sport=snapshot.sport,
            )
        )

    if not signals:
        return EvEvaluation(prediction, (), "no_edge", {"min_ev": config.min_ev, "sport": snapshot.sport})
    signals.sort(key=lambda signal: signal.expected_value, reverse=True)
    return EvEvaluation(prediction, tuple(signals), None, None)


# --------------------------------------------------------------------------
# Win-rate prediction model.
#
# The bot's purpose is a live, in-game over/under CALL graded by hit rate — no
# odds, no EV. At a fixed per-sport checkpoint (enough in-game signal, outcome
# still in doubt) the model locks ONE directional prediction against a reference
# line (Pinnacle's opening total), settled at game end. The checkpoint is what
# keeps the win rate honest: a call locked late, after the line is already
# decided, is a trivial "win" that inflates the rate.
# --------------------------------------------------------------------------

# Checkpoint in each sport's snapshot.minute units (soccer/basketball/hockey:
# elapsed minutes; baseball: inning index; tennis: set index). Roughly the
# game's midpoint — past it the in-game read is informative but the total is
# usually still undecided.
PREDICTION_CHECKPOINTS = {
    SPORT_SOCCER: 45,      # halftime
    SPORT_BASKETBALL: 24,  # halftime
    SPORT_HOCKEY: 30,      # midway (into the 2nd period)
    SPORT_BASEBALL: 4,     # entering the 4th inning
    SPORT_TENNIS: 2,       # into the 2nd set
}


_PREDICTION_ESTIMATOR = OddsEstimator()


@dataclass(frozen=True)
class TotalPrediction:
    side: str  # "over" | "under"
    line: float
    confidence: float  # model P(predicted side), in [0, 1]
    expected_total: float
    expected_remaining: float
    over_probability: float


def checkpoint_minute(sport: str) -> int:
    return PREDICTION_CHECKPOINTS.get(sport, 45)


def at_checkpoint(snapshot: LiveSnapshot) -> bool:
    """True once the game has reached its sport's prediction checkpoint."""
    return snapshot.minute >= checkpoint_minute(snapshot.sport)


def _predicted_remaining(snapshot: LiveSnapshot, line: float, config: StrategyConfig) -> float:
    """Expected remaining scoring, estimated from PACE and time left — NOT
    anchored to the line. The legacy estimate_expected_total_remaining() pulls
    toward `line - current`, which only makes sense for a live total; against a
    fixed pre-match line it badly misjudges late games (it thought ~6 runs were
    left in the 8th of a 2-run game). OddsEstimator's per-sport pace model is
    the line-independent read; fall back to the legacy estimate where it has no
    model (e.g. tennis)."""
    estimated = _PREDICTION_ESTIMATOR.estimate(snapshot, snapshot.sport)
    if estimated is not None:
        return estimated.expected_total_remaining
    return estimate_expected_total_remaining(snapshot, line, config.calibration_metrics)


def predict_total(
    snapshot: LiveSnapshot,
    line: float,
    config: StrategyConfig | None = None,
) -> TotalPrediction:
    """The model's directional over/under call against `line`, from current
    in-game performance. Picks the side it judges more likely; confidence is
    that side's probability. No odds, no EV — this is a prediction, not a bet."""
    config = config or StrategyConfig(strategy_version=sport_config(snapshot.sport).default_strategy)
    expected_remaining = _predicted_remaining(snapshot, line, config)
    max_remaining = _max_remaining_units(snapshot.sport, expected_remaining)
    over_prob = expected_total_exposure(
        snapshot.total_score, line, "over", expected_remaining, max_remaining
    ).fair_probability
    under_prob = expected_total_exposure(
        snapshot.total_score, line, "under", expected_remaining, max_remaining
    ).fair_probability
    side = "over" if over_prob >= under_prob else "under"
    return TotalPrediction(
        side=side,
        line=line,
        confidence=round(max(over_prob, under_prob), 4),
        expected_total=round(snapshot.total_score + expected_remaining, 3),
        expected_remaining=expected_remaining,
        over_probability=round(over_prob, 4),
    )


def estimate_expected_goals_remaining(
    snapshot: LiveSnapshot,
    live_total_line: float,
    history: HistoricalProfile | None = None,
) -> float:
    market_anchor = max(0.05, live_total_line - snapshot.total_score + 0.08)
    pace = snapshot.adjusted_shots_per_10
    dangerous_per_10 = _per_10(snapshot.total_dangerous_attacks, snapshot.minute)
    pace_multiplier = 1 + _clamp((pace - 2.0) * 0.18, -0.35, 0.55)
    danger_multiplier = 1 + _clamp((dangerous_per_10 - 8.0) * 0.025, -0.20, 0.25)
    red_card_multiplier = 1 + (0.08 * (snapshot.home_red_cards + snapshot.away_red_cards))
    market_lambda = market_anchor * pace_multiplier * danger_multiplier * red_card_multiplier

    if history and history.final_goal_totals:
        historical_remaining = max(0.05, (sum(history.final_goal_totals) / len(history.final_goal_totals)) - snapshot.total_score)
        return round(max(0.03, (market_lambda * 0.8) + (historical_remaining * 0.2)), 4)
    return round(max(0.03, market_lambda), 4)


def estimate_expected_total_remaining(
    snapshot: LiveSnapshot,
    live_total_line: float,
    calibration_metrics: dict | None = None,
) -> float:
    calibration_metrics = calibration_metrics or {}
    market_anchor = max(0.05, live_total_line - snapshot.total_score)
    if snapshot.sport == SPORT_HOCKEY:
        shot_count = snapshot.total_shots_on_target or _int_feature(snapshot.stats, "shots_on_goal")
        baseline_shot_rate = _metric(calibration_metrics, "shots_for_per_team_game", 21.0) * 2 / 60
        shot_multiplier = 1 + _clamp(((shot_count / max(snapshot.minute, 1)) - baseline_shot_rate) * 0.35, -0.25, 0.35)
        prior_total = _metric(calibration_metrics, "estimated_game_total_goals", 0.0)
        prior_remaining = prior_total * _remaining_fraction(snapshot.minute, 60) if prior_total else market_anchor
        blended = (market_anchor * 0.70) + (prior_remaining * 0.30)
        return round(max(0.03, blended * shot_multiplier), 4)
    if snapshot.sport == SPORT_BASKETBALL:
        regulation_minutes = 48
        elapsed = max(snapshot.minute, 1)
        pace_remaining = (snapshot.total_score / elapsed) * max(1, regulation_minutes - elapsed)
        prior_total = _metric(calibration_metrics, "estimated_game_total_points", 0.0)
        prior_remaining = prior_total * _remaining_fraction(snapshot.minute, regulation_minutes) if prior_total else market_anchor
        blended = (market_anchor * 0.65) + (prior_remaining * 0.20) + (pace_remaining * 0.15)
        return round(max(0.5, blended), 4)
    if snapshot.sport == SPORT_BASEBALL:
        inning = _inning_from_phase(snapshot.phase)
        if inning is None:
            return round(max(0.05, market_anchor), 4)
        remaining_factor = max(0.08, (9.5 - inning) / 9)
        prior_total = _metric(calibration_metrics, "estimated_game_total_runs", 0.0)
        prior_remaining = prior_total * remaining_factor if prior_total else market_anchor * remaining_factor
        return round(max(0.05, (market_anchor * 0.75) + (prior_remaining * 0.25)), 4)
    if snapshot.sport == SPORT_TENNIS:
        return round(max(0.5, market_anchor), 4)
    return round(max(0.03, market_anchor), 4)


def estimate_confidence(snapshot: LiveSnapshot, history: HistoricalProfile | None = None) -> float:
    sample = min(len(history.final_goal_totals), 50) if history else 0
    sample_score = sample / 50
    minute_score = 1.0 if 10 <= snapshot.minute <= 80 else 0.65
    stats_score = 1.0 if snapshot.total_shots_on_target + snapshot.total_shots_off_target > 0 else 0.75
    return round((sample_score * 0.35) + (minute_score * 0.35) + (stats_score * 0.30), 4)


def estimate_sport_confidence(snapshot: LiveSnapshot, calibration_status: str | None = None) -> float:
    state_score = 1.0 if snapshot.phase or snapshot.clock else 0.55
    odds_context_score = 0.8
    stat_score = 0.75 if snapshot.stats else 0.55
    if snapshot.sport in {SPORT_BASEBALL, SPORT_TENNIS}:
        stat_score *= 0.85
    calibration_score = 1.0 if calibration_status == "calibrated_baseline" else 0.45
    return round((state_score * 0.35) + (odds_context_score * 0.25) + (stat_score * 0.20) + (calibration_score * 0.20), 4)


def _meets_over(
    history: HistoricalProfile,
    model_line: float,
    thresholds: dict[float, tuple[int, float]],
) -> bool:
    threshold = thresholds.get(model_line)
    if threshold is None:
        return False
    goals, minimum_percent = threshold
    return history.final_over_percent(goals) >= minimum_percent


def _meets_under(
    history: HistoricalProfile,
    model_line: float,
    thresholds: dict[float, tuple[int, float]],
) -> bool:
    threshold = thresholds.get(model_line)
    if threshold is None:
        return False
    goals, maximum_percent = threshold
    return history.final_over_percent(goals) <= maximum_percent


def _meets_halftime_over(history: HistoricalProfile, model_line: float) -> bool:
    threshold = HT_OVER_THRESHOLDS.get(model_line)
    if threshold is None:
        return False
    goals, minimum_percent = threshold
    return history.halftime_over_percent(goals) >= minimum_percent


def _meets_halftime_under(history: HistoricalProfile, model_line: float) -> bool:
    threshold = HT_UNDER_THRESHOLDS.get(model_line)
    if threshold is None:
        return False
    goals, maximum_percent = threshold
    return history.halftime_over_percent(goals) <= maximum_percent


def _total_quotes_by_side(odds_quotes: tuple[OddsQuote, ...], market: str) -> dict[str, OddsQuote]:
    result: dict[str, OddsQuote] = {}
    for quote in odds_quotes:
        if quote.market == market and quote.period == "full_time" and quote.side in {"over", "under"}:
            result[quote.side] = quote
    return result


def _sport_state_gate(snapshot: LiveSnapshot) -> str | None:
    if snapshot.sport == SPORT_HOCKEY and not (snapshot.phase or snapshot.clock):
        return "hockey_missing_period_clock"
    if snapshot.sport == SPORT_BASKETBALL and not (snapshot.phase or snapshot.clock):
        return "basketball_missing_quarter_clock"
    if snapshot.sport == SPORT_BASEBALL and not snapshot.phase:
        return "baseball_missing_inning_state"
    if snapshot.sport == SPORT_TENNIS and not snapshot.phase:
        return "tennis_missing_set_state"
    return None


def _max_remaining_units(sport: str, expected_remaining: float) -> int:
    if sport == SPORT_BASKETBALL:
        return max(80, int(expected_remaining + 70))
    if sport == SPORT_TENNIS:
        return 60
    if sport == SPORT_BASEBALL:
        return 24
    return 12


def _inning_from_phase(phase: str | None) -> int | None:
    if not phase:
        return None
    digits = "".join(char for char in phase if char.isdigit())
    return int(digits) if digits else None


def _int_feature(stats: dict, key: str) -> int:
    value = stats.get(key)
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _metric(metrics: dict, key: str, default: float) -> float:
    try:
        return float(metrics.get(key, default))
    except (TypeError, ValueError):
        return default


def _remaining_fraction(elapsed: int, regulation_minutes: int) -> float:
    return max(0.02, min(1.0, (regulation_minutes - max(elapsed, 0)) / regulation_minutes))


def _per_10(value: int, minute: int) -> float:
    return (value / max(minute, 1)) * 10


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _age_seconds(captured_at: str) -> int:
    try:
        parsed = datetime.fromisoformat(captured_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max(0, int((datetime.now(UTC) - parsed).total_seconds()))
    except ValueError:
        return 0
