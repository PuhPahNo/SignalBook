"""Synthesize a fair total-market quote when the bookmaker hasn't posted one.

Anthony wants matches with missing two-sided total odds to still be eligible
for paper trades — but the synthetic quote is only emitted as a live signal
when the estimator's confidence clears the MIN_CONFIDENCE floor. The estimator
treats the result as a real paper trade (status='open', settled normally),
but tags every row with ``is_estimated=True`` plus a method + confidence so
the dashboard and post-hoc PnL split can keep estimated and market-priced
signals separate.

Design choices:

* Each sport's primary path is a remaining-time Poisson on the observed
  scoring pace, anchored against league prior when calibration is available.
  Reuses the same primitives strategy.py already uses for market-priced
  predictions, so estimated and market signals are scored against the same
  internal model — only the "where did this number come from" tag differs.
* Tennis is intentionally not supported (game totals are too noisy under
  pure score+set).
* Confidence is a weighted blend of (data completeness, elapsed-time signal,
  league sample size from historical_matches, market corroboration). It
  ranges 0..1; the live bot's emit floor is 0.50.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quicklin_soccer.models import LiveSnapshot, OddsQuote, now_iso
from quicklin_soccer.pricing import expected_total_exposure, poisson_pmf
from quicklin_soccer.sports import (
    SPORT_BASEBALL,
    SPORT_BASKETBALL,
    SPORT_HOCKEY,
    SPORT_SOCCER,
    SPORT_TENNIS,
    sport_config,
)


# Minimum confidence required for the estimator's output to be turned into
# a paper trade. Below this, cli.py still records a skipped_candidate row
# with the estimator output in details_json so we can tune the model.
MIN_CONFIDENCE: float = 0.50

# Realistic vig applied when synthesising the bookmaker-facing decimal odds
# from fair probabilities. Five percent per side is in line with the median
# AIScore-listed total_goals book.
DEFAULT_VIG: float = 0.05


_REGULATION_MINUTES = {
    SPORT_SOCCER: 90,
    SPORT_HOCKEY: 60,
    SPORT_BASKETBALL: 48,  # NBA-style; college is 40 but close enough for an estimate
    SPORT_BASEBALL: 9,  # innings, not minutes — handled specially below
}


@dataclass(frozen=True)
class EstimatedOdds:
    """The synthesized two-sided total market for a single snapshot."""

    line: float
    over_odds: float
    under_odds: float
    method: str
    confidence: float
    fair_over_probability: float
    fair_under_probability: float
    expected_total_remaining: float
    notes: dict[str, float | str | None]


class OddsEstimator:
    """Stateless estimator. One instance is fine to reuse across scans."""

    def estimate(
        self,
        snapshot: LiveSnapshot,
        sport: str,
        historical_sample_size: int = 0,
        corroborating_market: bool = False,
    ) -> EstimatedOdds | None:
        if sport == SPORT_TENNIS:
            return None
        if sport == SPORT_SOCCER:
            return self._estimate_soccer(
                snapshot, historical_sample_size, corroborating_market
            )
        if sport in (SPORT_HOCKEY, SPORT_BASKETBALL):
            return self._estimate_time_clocked(
                snapshot, sport, historical_sample_size, corroborating_market
            )
        if sport == SPORT_BASEBALL:
            return self._estimate_baseball(
                snapshot, historical_sample_size, corroborating_market
            )
        return None

    # ----------------------------------------------------------------- soccer

    def _estimate_soccer(
        self,
        snapshot: LiveSnapshot,
        historical_sample_size: int,
        corroborating_market: bool,
    ) -> EstimatedOdds | None:
        minute = max(1, min(snapshot.minute, 90))
        remaining = max(0, 90 - minute)
        current_total = snapshot.total_score

        # Per-minute scoring pace. We blend the in-match pace with a league
        # prior of ~2.7 goals per 90 to keep early-match estimates from
        # being whipsawed by a single early goal.
        in_match_per_min = current_total / minute if minute else 0.0
        league_prior_per_min = 2.7 / 90.0
        prior_weight = _clamp(1.0 - minute / 60.0, 0.15, 0.7)
        blended_per_min = (
            in_match_per_min * (1 - prior_weight)
            + league_prior_per_min * prior_weight
        )

        # Adjust pace by the in-match shot intensity if we have it.
        shot_signal = snapshot.adjusted_shots_per_10
        if shot_signal:
            pace_multiplier = 1 + _clamp((shot_signal - 2.0) * 0.10, -0.30, 0.45)
            blended_per_min *= pace_multiplier

        lam_remaining = max(0.05, blended_per_min * remaining)
        expected_total = current_total + lam_remaining

        line = _half_line_above(expected_total)
        over_prob, under_prob = _poisson_over_under(
            lam_remaining, current_total, line
        )
        over_odds, under_odds = _add_vig(over_prob, under_prob, DEFAULT_VIG)
        confidence = self._confidence(
            data_completeness=_soccer_completeness(snapshot),
            minute=minute,
            regulation_minutes=90,
            historical_sample_size=historical_sample_size,
            corroborating_market=corroborating_market,
        )
        return EstimatedOdds(
            line=line,
            over_odds=over_odds,
            under_odds=under_odds,
            method="poisson_soccer",
            confidence=confidence,
            fair_over_probability=over_prob,
            fair_under_probability=under_prob,
            expected_total_remaining=lam_remaining,
            notes={
                "blended_per_min": round(blended_per_min, 5),
                "minute": minute,
                "current_total": current_total,
            },
        )

    # ------------------------------------------------ hockey / basketball

    def _estimate_time_clocked(
        self,
        snapshot: LiveSnapshot,
        sport: str,
        historical_sample_size: int,
        corroborating_market: bool,
    ) -> EstimatedOdds | None:
        regulation = _REGULATION_MINUTES[sport]
        elapsed = max(1, min(snapshot.minute, regulation))
        remaining = max(0, regulation - elapsed)
        current_total = snapshot.total_score
        in_match_per_min = current_total / elapsed if elapsed else 0.0

        # League priors. These are intentionally rough; the confidence
        # formula penalises early-game estimates so a wrong prior gets
        # mostly filtered out before emit.
        league_prior_total = {
            SPORT_HOCKEY: 6.0,  # ~6 goals / game
            SPORT_BASKETBALL: 222.0,  # NBA-ish
        }[sport]
        league_prior_per_min = league_prior_total / regulation
        prior_weight = _clamp(1.0 - elapsed / max(regulation * 0.6, 1.0), 0.15, 0.7)
        blended_per_min = (
            in_match_per_min * (1 - prior_weight)
            + league_prior_per_min * prior_weight
        )
        lam_remaining = max(0.05, blended_per_min * remaining)
        expected_total = current_total + lam_remaining
        line = _half_line_above(expected_total)
        over_prob, under_prob = _poisson_over_under(
            lam_remaining, current_total, line
        )
        over_odds, under_odds = _add_vig(over_prob, under_prob, DEFAULT_VIG)
        confidence = self._confidence(
            data_completeness=_clock_completeness(snapshot),
            minute=elapsed,
            regulation_minutes=regulation,
            historical_sample_size=historical_sample_size,
            corroborating_market=corroborating_market,
        )
        # Cap basketball confidence slightly lower — points aren't Poisson
        # and the variance shows when applied naively. Still good enough as
        # a watchlist-grade input.
        if sport == SPORT_BASKETBALL:
            confidence = min(confidence, 0.75)
        return EstimatedOdds(
            line=line,
            over_odds=over_odds,
            under_odds=under_odds,
            method=f"poisson_{sport}",
            confidence=confidence,
            fair_over_probability=over_prob,
            fair_under_probability=under_prob,
            expected_total_remaining=lam_remaining,
            notes={
                "blended_per_min": round(blended_per_min, 5),
                "elapsed_minutes": elapsed,
                "regulation_minutes": regulation,
                "current_total": current_total,
            },
        )

    # ---------------------------------------------------------------- baseball

    def _estimate_baseball(
        self,
        snapshot: LiveSnapshot,
        historical_sample_size: int,
        corroborating_market: bool,
    ) -> EstimatedOdds | None:
        # snapshot.minute is repurposed by aiscore.py to mean "inning index"
        # for baseball. Anything past inning 9 is extras.
        inning = max(1, snapshot.minute)
        if inning > 12:
            inning = 12  # don't extrapolate runaway extras
        remaining_innings = max(0, 9 - inning)
        current_total = snapshot.total_score
        per_inning = current_total / inning if inning else 0.0
        league_prior_per_inning = 9.0 / 9.0  # ~9 runs/game baseline
        prior_weight = _clamp(1.0 - inning / 6.0, 0.20, 0.65)
        blended_per_inning = (
            per_inning * (1 - prior_weight)
            + league_prior_per_inning * prior_weight
        )
        lam_remaining = max(0.05, blended_per_inning * remaining_innings)
        expected_total = current_total + lam_remaining
        line = _half_line_above(expected_total)
        over_prob, under_prob = _poisson_over_under(
            lam_remaining, current_total, line
        )
        over_odds, under_odds = _add_vig(over_prob, under_prob, DEFAULT_VIG)
        confidence = self._confidence(
            data_completeness=_clock_completeness(snapshot),
            minute=inning,
            regulation_minutes=9,
            historical_sample_size=historical_sample_size,
            corroborating_market=corroborating_market,
        )
        # Runs aren't a clean Poisson; clamp confidence so the bot doesn't
        # over-trust early-inning estimates.
        confidence = min(confidence, 0.65)
        return EstimatedOdds(
            line=line,
            over_odds=over_odds,
            under_odds=under_odds,
            method="poisson_baseball",
            confidence=confidence,
            fair_over_probability=over_prob,
            fair_under_probability=under_prob,
            expected_total_remaining=lam_remaining,
            notes={
                "blended_per_inning": round(blended_per_inning, 5),
                "inning": inning,
                "current_total": current_total,
            },
        )

    # -------------------------------------------------------------- confidence

    def _confidence(
        self,
        data_completeness: float,
        minute: int,
        regulation_minutes: int,
        historical_sample_size: int,
        corroborating_market: bool,
    ) -> float:
        # Sigmoid-ish ramp: ~0 at minute 0, ~0.78 at half-time, ~0.99 at full.
        elapsed_fraction = minute / max(regulation_minutes, 1)
        elapsed_signal = 1 - math.exp(-elapsed_fraction * 2.5)
        sample_signal = min(historical_sample_size / 30.0, 1.0)
        corroboration = 1.0 if corroborating_market else 0.5
        score = (
            0.35 * data_completeness
            + 0.30 * elapsed_signal
            + 0.20 * sample_signal
            + 0.15 * corroboration
        )
        return round(_clamp(score, 0.0, 1.0), 3)


# --------------------------------------------------------------------- helpers


def estimated_odds_to_quotes(
    estimated: EstimatedOdds,
    snapshot: LiveSnapshot,
    sport: str,
) -> tuple[OddsQuote, OddsQuote]:
    """Wrap an EstimatedOdds payload into the two OddsQuote objects the
    strategy and storage layers already know how to consume."""
    market = sport_config(sport).total_market
    captured_at = snapshot.captured_at or now_iso()
    base = dict(
        provider="aiscore",
        provider_match_id=snapshot.provider_match_id,
        canonical_id=snapshot.canonical_id,
        captured_at=captured_at,
        market=market,
        period="full_time",
        line=estimated.line,
        bookmaker="estimator",
        sport=sport,
        is_estimated=True,
        estimation_method=estimated.method,
        estimation_confidence=estimated.confidence,
        raw={
            "source": "odds_estimator",
            "method": estimated.method,
            "confidence": estimated.confidence,
            "notes": estimated.notes,
        },
    )
    over = OddsQuote(side="over", decimal_odds=estimated.over_odds, **base)
    under = OddsQuote(side="under", decimal_odds=estimated.under_odds, **base)
    return over, under


def _poisson_over_under(
    lam_remaining: float, current_total: int, line: float
) -> tuple[float, float]:
    """Probability that final_total > line and < line, respectively."""
    # We integrate the Poisson PMF for remaining goals up to a generous tail.
    max_tail = max(20, int(lam_remaining * 6) + current_total + 5)
    over_prob = 0.0
    under_prob = 0.0
    for remaining in range(max_tail + 1):
        p = poisson_pmf(lam_remaining, remaining)
        final_total = current_total + remaining
        if final_total > line:
            over_prob += p
        elif final_total < line:
            under_prob += p
    # Pull anything still in the tail onto over (final_total > line).
    leftover = max(0.0, 1.0 - over_prob - under_prob)
    over_prob += leftover
    # Clamp away exact zeros so the synthesized decimal odds stay finite.
    over_prob = _clamp(over_prob, 0.01, 0.99)
    under_prob = _clamp(under_prob, 0.01, 0.99)
    # Renormalize.
    total = over_prob + under_prob
    return over_prob / total, under_prob / total


def _add_vig(
    fair_over_prob: float, fair_under_prob: float, vig_per_side: float
) -> tuple[float, float]:
    """Apply a per-side vig so the synthesized odds look like a real book.
    Without this the EV math compares a no-vig synthesised price to a
    no-vig synthesised price and almost always lands at zero EV."""
    over_with_vig = fair_over_prob / (1 + vig_per_side)
    under_with_vig = fair_under_prob / (1 + vig_per_side)
    over_decimal = 1.0 / max(over_with_vig, 1e-6)
    under_decimal = 1.0 / max(under_with_vig, 1e-6)
    return round(over_decimal, 3), round(under_decimal, 3)


def _half_line_above(value: float) -> float:
    """Pick a 0.5-bucket line just above the expected total — matches the
    bucket size most books and our strategy code already use for totals."""
    return math.floor(value) + 0.5


def _soccer_completeness(snapshot: LiveSnapshot) -> float:
    have = 0
    needed = 0
    for value in (snapshot.minute, snapshot.home_score is not None, snapshot.away_score is not None):
        needed += 1
        if value:
            have += 1
    if snapshot.adjusted_total_shots > 0:
        have += 1
    needed += 1
    return have / needed if needed else 0.0


def _clock_completeness(snapshot: LiveSnapshot) -> float:
    needed = 3
    have = 0
    if snapshot.minute:
        have += 1
    if snapshot.home_score is not None or snapshot.away_score is not None:
        have += 1
    if snapshot.phase or snapshot.clock:
        have += 1
    return have / needed


def _clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value
