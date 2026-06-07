from __future__ import annotations

from dataclasses import dataclass


SPORT_SOCCER = "soccer"
SPORT_HOCKEY = "hockey"
SPORT_BASKETBALL = "basketball"
SPORT_BASEBALL = "baseball"
SPORT_TENNIS = "tennis"

SUPPORTED_SPORTS = (SPORT_SOCCER, SPORT_HOCKEY, SPORT_BASKETBALL, SPORT_BASEBALL, SPORT_TENNIS)


@dataclass(frozen=True)
class SportConfig:
    sport: str
    aiscore_url: str
    total_market: str
    default_strategy: str
    total_unit_label: str


SPORT_CONFIGS = {
    SPORT_SOCCER: SportConfig(SPORT_SOCCER, "https://www.aiscore.com/", "total_goals", "ev_totals_v1", "goals"),
    SPORT_HOCKEY: SportConfig(SPORT_HOCKEY, "https://www.aiscore.com/ice-hockey/", "total_goals", "hockey_totals_v1", "goals"),
    SPORT_BASKETBALL: SportConfig(SPORT_BASKETBALL, "https://www.aiscore.com/basketball", "total_points", "basketball_totals_v1", "points"),
    SPORT_BASEBALL: SportConfig(SPORT_BASEBALL, "https://www.aiscore.com/baseball", "total_runs", "baseball_totals_v1", "runs"),
    SPORT_TENNIS: SportConfig(SPORT_TENNIS, "https://www.aiscore.com/tennis", "total_games", "tennis_match_totals_v1", "games"),
}


SPORT_STRATEGIES = tuple(config.default_strategy for config in SPORT_CONFIGS.values())


def sport_config(sport: str) -> SportConfig:
    if sport not in SPORT_CONFIGS:
        raise ValueError(f"unsupported sport: {sport}")
    return SPORT_CONFIGS[sport]


def default_strategy_for_sport(sport: str) -> str:
    return sport_config(sport).default_strategy
