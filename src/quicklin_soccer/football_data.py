from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from quicklin_soccer.pricing import no_vig_probabilities, settle_total_bet
from quicklin_soccer.storage import QuicklinStore


FOOTBALL_DATA_SOURCE = "football-data.co.uk"


def import_football_data_csv(store: QuicklinStore, path: Path, season: str | None = None) -> int:
    imported = 0
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for raw in reader:
            if not raw.get("HomeTeam") or not raw.get("AwayTeam"):
                continue
            row = _normalize_row(raw, path, season)
            store.insert_historical_match(row)
            imported += 1
    store.conn.commit()
    return imported


def backtest_historical_baseline(store: QuicklinStore, min_ev: float = 0.03, min_history: int = 6) -> dict[str, Any]:
    rows = store.historical_matches()
    league_goals: list[int] = []
    team_goals: dict[str, list[int]] = defaultdict(list)
    bets: list[dict[str, Any]] = []
    brier_values: list[float] = []
    logloss_values: list[float] = []
    calibration: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "predicted": 0.0, "actual": 0.0})

    for row in rows:
        home_team = row["home_team"]
        away_team = row["away_team"]
        final_total = _int(row["fthg"]) + _int(row["ftag"])
        actual_over = 1.0 if final_total > 2.5 else 0.0
        prior = _rolling_over_probability(league_goals, team_goals[home_team], team_goals[away_team])

        over_odds = row["over25_odds"]
        under_odds = row["under25_odds"]
        if over_odds and under_odds:
            market_over, market_under = no_vig_probabilities(float(over_odds), float(under_odds))
            predicted_over = (prior * 0.45) + (market_over * 0.55)
            predicted_under = 1 - predicted_over
            brier_values.append((predicted_over - actual_over) ** 2)
            logloss_values.append(_logloss(predicted_over, actual_over))
            bucket = _bucket(predicted_over)
            calibration[bucket]["n"] += 1
            calibration[bucket]["predicted"] += predicted_over
            calibration[bucket]["actual"] += actual_over

            if len(league_goals) >= min_history:
                over_ev = (predicted_over * (float(over_odds) - 1)) - (1 - predicted_over)
                under_ev = (predicted_under * (float(under_odds) - 1)) - (1 - predicted_under)
                if over_ev >= min_ev or under_ev >= min_ev:
                    if over_ev >= under_ev:
                        side = "over"
                        odds = float(over_odds)
                        model_prob = predicted_over
                        ev = over_ev
                    else:
                        side = "under"
                        odds = float(under_odds)
                        model_prob = predicted_under
                        ev = under_ev
                    result, payout = settle_total_bet(final_total, 2.5, side, odds)
                    close_odds = _closing_odds(row, side)
                    bets.append(
                        {
                            "date": row["match_date"],
                            "match": f"{home_team} vs {away_team}",
                            "side": side,
                            "line": 2.5,
                            "odds": odds,
                            "model_probability": model_prob,
                            "ev": ev,
                            "result": result,
                            "payout_units": payout,
                            "closing_line_value_proxy": (odds - close_odds) if close_odds else None,
                        }
                    )

        league_goals.append(final_total)
        team_goals[home_team].append(final_total)
        team_goals[away_team].append(final_total)

    stakes = len(bets)
    profit = round(sum(bet["payout_units"] for bet in bets), 6)
    wins = sum(1 for bet in bets if bet["payout_units"] > 0)
    calibration_rows = {}
    for bucket, values in calibration.items():
        n = values["n"]
        calibration_rows[bucket] = {
            "n": int(n),
            "avg_predicted": round(values["predicted"] / n, 4) if n else 0.0,
            "actual_rate": round(values["actual"] / n, 4) if n else 0.0,
        }

    return {
        "strategy_version": "historical_baseline_v1",
        "matches": len(rows),
        "bets": stakes,
        "profit_units": profit,
        "roi": round(profit / stakes, 4) if stakes else 0.0,
        "yield": round(profit / stakes, 4) if stakes else 0.0,
        "hit_rate": round(wins / stakes, 4) if stakes else 0.0,
        "brier_score": round(sum(brier_values) / len(brier_values), 4) if brier_values else None,
        "log_loss": round(sum(logloss_values) / len(logloss_values), 4) if logloss_values else None,
        "calibration": calibration_rows,
        "bets_detail": bets,
        "note": "Historical baseline only; free data does not contain live in-play odds snapshots.",
    }


def _normalize_row(raw: dict[str, str], path: Path, season: str | None) -> dict[str, Any]:
    return {
        "source": FOOTBALL_DATA_SOURCE,
        "division": raw.get("Div"),
        "season": season or path.stem,
        "match_date": raw.get("Date"),
        "home_team": raw.get("HomeTeam"),
        "away_team": raw.get("AwayTeam"),
        "fthg": _optional_int(raw.get("FTHG")),
        "ftag": _optional_int(raw.get("FTAG")),
        "hthg": _optional_int(raw.get("HTHG")),
        "htag": _optional_int(raw.get("HTAG")),
        "home_shots": _optional_int(raw.get("HS")),
        "away_shots": _optional_int(raw.get("AS")),
        "home_shots_target": _optional_int(raw.get("HST")),
        "away_shots_target": _optional_int(raw.get("AST")),
        "home_corners": _optional_int(raw.get("HC")),
        "away_corners": _optional_int(raw.get("AC")),
        "home_red_cards": _optional_int(raw.get("HR")),
        "away_red_cards": _optional_int(raw.get("AR")),
        "over25_odds": _optional_float(_first(raw, "Avg>2.5", "B365>2.5", "Max>2.5", "P>2.5")),
        "under25_odds": _optional_float(_first(raw, "Avg<2.5", "B365<2.5", "Max<2.5", "P<2.5")),
        "close_over25_odds": _optional_float(_first(raw, "C>2.5", "PC>2.5", "Avg>2.5")),
        "close_under25_odds": _optional_float(_first(raw, "C<2.5", "PC<2.5", "Avg<2.5")),
        "raw": raw,
    }


def _rolling_over_probability(league_goals: list[int], home_goals: list[int], away_goals: list[int]) -> float:
    values = (home_goals[-8:] + away_goals[-8:] + league_goals[-30:])
    if not values:
        return 0.5
    over_rate = sum(1 for goals in values if goals > 2.5) / len(values)
    avg_goals = sum(values) / len(values)
    goal_score = 1 / (1 + math.exp(-1.1 * (avg_goals - 2.5)))
    return (over_rate * 0.65) + (goal_score * 0.35)


def _first(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _optional_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _int(value) -> int:
    return 0 if value is None else int(value)


def _logloss(probability: float, actual: float) -> float:
    probability = min(0.999, max(0.001, probability))
    return -((actual * math.log(probability)) + ((1 - actual) * math.log(1 - probability)))


def _bucket(probability: float) -> str:
    low = int(probability * 10) * 10
    high = low + 10
    return f"{low:02d}-{high:02d}%"


def _closing_odds(row, side: str) -> float | None:
    key = "close_over25_odds" if side == "over" else "close_under25_odds"
    return float(row[key]) if row[key] else None
