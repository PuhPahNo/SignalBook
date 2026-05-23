from __future__ import annotations

import math
from dataclasses import dataclass


SUPPORTED_LINE_FRACTIONS = {0.0, 0.25, 0.5, 0.75}


@dataclass(frozen=True)
class Exposure:
    win_units: float
    loss_units: float
    push_units: float

    @property
    def fair_odds(self) -> float | None:
        if self.win_units <= 0:
            return None
        return 1 + (self.loss_units / self.win_units)

    @property
    def fair_probability(self) -> float:
        odds = self.fair_odds
        return 0.0 if odds is None else 1 / odds

    def expected_value(self, decimal_odds: float) -> float:
        return (self.win_units * (decimal_odds - 1)) - self.loss_units


def no_vig_probabilities(over_odds: float, under_odds: float) -> tuple[float, float]:
    over_implied = 1 / over_odds
    under_implied = 1 / under_odds
    total = over_implied + under_implied
    if total <= 0:
        return 0.0, 0.0
    return over_implied / total, under_implied / total


def poisson_probability_at_or_above(lam: float, threshold: int) -> float:
    if threshold <= 0:
        return 1.0
    return 1 - sum(poisson_pmf(lam, goals) for goals in range(threshold))


def poisson_pmf(lam: float, goals: int) -> float:
    if lam <= 0:
        return 1.0 if goals == 0 else 0.0
    return math.exp(-lam + (goals * math.log(lam)) - math.lgamma(goals + 1))


def expected_total_exposure(
    current_total: int,
    line: float,
    side: str,
    expected_goals_remaining: float,
    max_remaining_goals: int = 12,
) -> Exposure:
    _validate_side(side)
    win_units = 0.0
    loss_units = 0.0
    push_units = 0.0

    for remaining_goals in range(max_remaining_goals + 1):
        probability = poisson_pmf(expected_goals_remaining, remaining_goals)
        final_total = current_total + remaining_goals
        for component_line, weight in split_total_line(line):
            outcome = settle_total_component(final_total, component_line, side)
            if outcome == "win":
                win_units += probability * weight
            elif outcome == "loss":
                loss_units += probability * weight
            else:
                push_units += probability * weight

    tail_probability = max(0.0, 1 - (win_units + loss_units + push_units))
    if tail_probability:
        tail_total = current_total + max_remaining_goals + 1
        for component_line, weight in split_total_line(line):
            outcome = settle_total_component(tail_total, component_line, side)
            if outcome == "win":
                win_units += tail_probability * weight
            elif outcome == "loss":
                loss_units += tail_probability * weight
            else:
                push_units += tail_probability * weight

    return Exposure(win_units=win_units, loss_units=loss_units, push_units=push_units)


def settle_total_bet(final_total: int, line: float, side: str, decimal_odds: float, stake_units: float = 1.0) -> tuple[str, float]:
    _validate_side(side)
    payout = 0.0
    wins = 0
    losses = 0
    pushes = 0

    for component_line, weight in split_total_line(line):
        component_stake = stake_units * weight
        outcome = settle_total_component(final_total, component_line, side)
        if outcome == "win":
            wins += 1
            payout += component_stake * (decimal_odds - 1)
        elif outcome == "loss":
            losses += 1
            payout -= component_stake
        else:
            pushes += 1

    if wins and losses:
        result = "split"
    elif wins and pushes:
        result = "half_win"
    elif losses and pushes:
        result = "half_loss"
    elif wins:
        result = "win"
    elif losses:
        result = "loss"
    else:
        result = "push"

    return result, round(payout, 6)


def settle_total_component(final_total: int, line: float, side: str) -> str:
    if side == "over":
        if final_total > line:
            return "win"
        if final_total < line:
            return "loss"
        return "push"
    if final_total < line:
        return "win"
    if final_total > line:
        return "loss"
    return "push"


def split_total_line(line: float) -> tuple[tuple[float, float], ...]:
    whole = math.floor(line)
    fraction = round(line - whole, 2)
    if fraction not in SUPPORTED_LINE_FRACTIONS:
        raise ValueError(f"unsupported total line: {line}")
    if fraction == 0.25:
        return ((float(whole), 0.5), (whole + 0.5, 0.5))
    if fraction == 0.75:
        return ((whole + 0.5, 0.5), (float(whole + 1), 0.5))
    return ((float(line), 1.0),)


def is_supported_total_line(line: float | None) -> bool:
    if line is None:
        return False
    fraction = round(line - math.floor(line), 2)
    return fraction in SUPPORTED_LINE_FRACTIONS


def _validate_side(side: str) -> None:
    if side not in {"over", "under"}:
        raise ValueError(f"unsupported side: {side}")
