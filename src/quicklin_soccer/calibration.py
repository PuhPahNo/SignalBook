from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from quicklin_soccer.football_data import backtest_historical_baseline
from quicklin_soccer.models import now_iso
from quicklin_soccer.sports import (
    SPORT_BASEBALL,
    SPORT_BASKETBALL,
    SPORT_HOCKEY,
    SPORT_SOCCER,
    SPORT_TENNIS,
    SUPPORTED_SPORTS,
    default_strategy_for_sport,
)


FOOTBALL_DATA_URL = "https://www.football-data.co.uk/data"
NHL_TEAM_SUMMARY_URL = "https://api.nhle.com/stats/rest/en/team/summary?cayenneExp=seasonId={season_id}%20and%20gameTypeId=2"
NHL_SOURCE_URL = "https://www.nhl.com/stats/teams"
NBA_SOURCE_URL = "https://www.nba.com/stats/teams/traditional"
NBA_API_URL = "https://stats.nba.com/stats/leaguedashteamstats"
MLB_TEAM_HITTING_URL = "https://statsapi.mlb.com/api/v1/teams/stats?stats=season&group=hitting&sportIds=1&season={season}"
MLB_SOURCE_URL = "https://baseballsavant.mlb.com/"
ATP_SOURCE_URL = "https://www.atptour.com/en/stats"


@dataclass(frozen=True)
class CalibrationResult:
    sport: str
    strategy_version: str
    source: str
    source_url: str
    season: str | None
    status: str
    sample_size: int
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: str | None = None
    created_at: str = field(default_factory=now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "strategy_version": self.strategy_version,
            "source": self.source,
            "source_url": self.source_url,
            "season": self.season,
            "status": self.status,
            "sample_size": self.sample_size,
            "metrics": self.metrics,
            "notes": self.notes,
            "created_at": self.created_at,
        }


def run_calibrations(sport: str = "all", season: str | None = None, store: Any | None = None) -> list[CalibrationResult]:
    sports = SUPPORTED_SPORTS if sport == "all" else (sport,)
    return [calibrate_sport(item, season=season, store=store) for item in sports]


def calibrate_sport(sport: str, season: str | None = None, store: Any | None = None) -> CalibrationResult:
    try:
        if sport == SPORT_SOCCER:
            return calibrate_soccer(store, season)
        if sport == SPORT_HOCKEY:
            return calibrate_hockey(season)
        if sport == SPORT_BASKETBALL:
            return calibrate_basketball(season)
        if sport == SPORT_BASEBALL:
            return calibrate_baseball(season)
        if sport == SPORT_TENNIS:
            return calibrate_tennis(season)
    except Exception as exc:
        return _result(
            sport,
            source=_source_name(sport),
            source_url=_source_url(sport),
            season=season,
            status="source_unavailable",
            sample_size=0,
            notes=f"{type(exc).__name__}: {exc}",
        )
    raise ValueError(f"unsupported sport: {sport}")


def calibrate_soccer(store: Any | None, season: str | None = None) -> CalibrationResult:
    if store is None:
        return _result(
            SPORT_SOCCER,
            source="Football-Data local import",
            source_url=FOOTBALL_DATA_URL,
            season=season,
            status="needs_historical_import",
            sample_size=0,
            notes="Run signalbook backtest with Football-Data CSVs before soccer calibration can be refreshed.",
        )
    historical = store.historical_matches()
    if not historical:
        return _result(
            SPORT_SOCCER,
            source="Football-Data local import",
            source_url=FOOTBALL_DATA_URL,
            season=season,
            status="needs_historical_import",
            sample_size=0,
            notes="No imported Football-Data matches are available in SQLite yet.",
        )

    backtest = backtest_historical_baseline(store)
    metrics = {
        "roi": backtest.get("roi"),
        "yield": backtest.get("yield"),
        "hit_rate": backtest.get("hit_rate"),
        "brier_score": backtest.get("brier_score"),
        "log_loss": backtest.get("log_loss"),
        "signals": backtest.get("signals"),
        "calibration": backtest.get("calibration", {}),
    }
    return _result(
        SPORT_SOCCER,
        source="Football-Data local import",
        source_url=FOOTBALL_DATA_URL,
        season=season,
        status="calibrated_baseline",
        sample_size=int(backtest.get("matches", len(historical))),
        metrics=metrics,
        notes="Historical soccer baseline only; this is not a live in-play odds backtest.",
    )


def calibrate_hockey(season: str | None = None) -> CalibrationResult:
    season_id = _nhl_season_id(season)
    url = NHL_TEAM_SUMMARY_URL.format(season_id=season_id)
    data = _fetch_json(url, headers=_browser_headers(NHL_SOURCE_URL))
    metrics = hockey_metrics_from_nhl_summary(data)
    return _result(
        SPORT_HOCKEY,
        source="NHL team summary",
        source_url=NHL_SOURCE_URL,
        season=season_id,
        status="calibrated_baseline",
        sample_size=int(metrics["teams"]),
        metrics=metrics,
        notes="League-rate baseline for totals; not a proof of betting edge.",
    )


def calibrate_basketball(season: str | None = None) -> CalibrationResult:
    season_label = _basketball_season_label(season)
    base_data = _fetch_json(_nba_url("Base", season_label), headers=_browser_headers(NBA_SOURCE_URL))
    advanced_data = _fetch_json(_nba_url("Advanced", season_label), headers=_browser_headers(NBA_SOURCE_URL))
    metrics = basketball_metrics_from_nba_stats(base_data, advanced_data)
    return _result(
        SPORT_BASKETBALL,
        source="NBA team stats",
        source_url=NBA_SOURCE_URL,
        season=season_label,
        status="calibrated_baseline",
        sample_size=int(metrics["teams"]),
        metrics=metrics,
        notes="Team per-game and pace baseline; not a possession-level live model yet.",
    )


def calibrate_baseball(season: str | None = None) -> CalibrationResult:
    season_label = season or _last_completed_baseball_season()
    data = _fetch_json(MLB_TEAM_HITTING_URL.format(season=urllib.parse.quote(str(season_label))))
    metrics = baseball_metrics_from_mlb_hitting(data)
    return _result(
        SPORT_BASEBALL,
        source="MLB Stats API team hitting",
        source_url=MLB_SOURCE_URL,
        season=str(season_label),
        status="calibrated_baseline",
        sample_size=int(metrics["teams"]),
        metrics=metrics,
        notes="League run-rate baseline; v1 still lacks base/out and pitcher context.",
    )


def calibrate_tennis(season: str | None = None) -> CalibrationResult:
    return _result(
        SPORT_TENNIS,
        source="ATP Stats public page",
        source_url=ATP_SOURCE_URL,
        season=season,
        status="manual_source_required",
        sample_size=0,
        metrics={},
        notes="ATP exposes public stats pages, but no stable free JSON endpoint is wired here. Tennis signals remain skipped unless AIScore exposes supported totals.",
    )


def hockey_metrics_from_nhl_summary(data: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in data.get("data", []) if _number(row.get("gamesPlayed")) > 0]
    if not rows:
        raise ValueError("NHL team summary returned no team rows")
    goals_per_team_game = _weighted_per_game(rows, "goalsFor", "goalsForPerGame")
    goals_against_per_team_game = _weighted_per_game(rows, "goalsAgainst", "goalsAgainstPerGame")
    shots_for_per_team_game = _weighted_average(rows, "shotsForPerGame", "gamesPlayed")
    shots_against_per_team_game = _weighted_average(rows, "shotsAgainstPerGame", "gamesPlayed")
    return {
        "teams": len(rows),
        "games_per_team_avg": round(sum(_number(row.get("gamesPlayed")) for row in rows) / len(rows), 3),
        "goals_per_team_game": round(goals_per_team_game, 4),
        "goals_against_per_team_game": round(goals_against_per_team_game, 4),
        "estimated_game_total_goals": round(goals_per_team_game * 2, 4),
        "shots_for_per_team_game": round(shots_for_per_team_game, 4),
        "shots_against_per_team_game": round(shots_against_per_team_game, 4),
        "goals_per_shot": round(goals_per_team_game / max(shots_for_per_team_game, 0.01), 5),
    }


def basketball_metrics_from_nba_stats(base_data: dict[str, Any], advanced_data: dict[str, Any]) -> dict[str, Any]:
    base_rows = _nba_rows(base_data)
    advanced_rows = _nba_rows(advanced_data)
    if not base_rows:
        raise ValueError("NBA base stats returned no team rows")
    advanced_by_team = {row["TEAM_ID"]: row for row in advanced_rows}
    pace_rows = [row for row in base_rows if row.get("TEAM_ID") in advanced_by_team]
    points_per_team_game = _weighted_average(base_rows, "PTS", "GP")
    pace = _weighted_average([advanced_by_team[row["TEAM_ID"]] for row in pace_rows], "PACE", "GP") if pace_rows else 0
    return {
        "teams": len(base_rows),
        "games_per_team_avg": round(_weighted_average(base_rows, "GP", "TEAM_COUNT"), 3),
        "points_per_team_game": round(points_per_team_game, 4),
        "estimated_game_total_points": round(points_per_team_game * 2, 4),
        "pace": round(pace, 4),
        "field_goal_attempts_per_team_game": round(_weighted_average(base_rows, "FGA", "GP"), 4),
        "three_point_attempts_per_team_game": round(_weighted_average(base_rows, "FG3A", "GP"), 4),
        "free_throw_attempts_per_team_game": round(_weighted_average(base_rows, "FTA", "GP"), 4),
        "turnovers_per_team_game": round(_weighted_average(base_rows, "TOV", "GP"), 4),
    }


def baseball_metrics_from_mlb_hitting(data: dict[str, Any]) -> dict[str, Any]:
    splits = data.get("stats", [{}])[0].get("splits", [])
    rows = [row.get("stat", {}) for row in splits if _number(row.get("stat", {}).get("gamesPlayed")) > 0]
    if not rows:
        raise ValueError("MLB team hitting returned no team rows")
    games = sum(_number(row.get("gamesPlayed")) for row in rows)
    runs = sum(_number(row.get("runs")) for row in rows)
    hits = sum(_number(row.get("hits")) for row in rows)
    home_runs = sum(_number(row.get("homeRuns")) for row in rows)
    walks = sum(_number(row.get("baseOnBalls")) for row in rows)
    return {
        "teams": len(rows),
        "games_per_team_avg": round(games / len(rows), 3),
        "runs_per_team_game": round(runs / games, 4),
        "estimated_game_total_runs": round((runs / games) * 2, 4),
        "hits_per_team_game": round(hits / games, 4),
        "home_runs_per_team_game": round(home_runs / games, 4),
        "walks_per_team_game": round(walks / games, 4),
    }


def _result(
    sport: str,
    *,
    source: str,
    source_url: str,
    season: str | None,
    status: str,
    sample_size: int,
    metrics: dict[str, Any] | None = None,
    notes: str | None = None,
) -> CalibrationResult:
    return CalibrationResult(
        sport=sport,
        strategy_version=default_strategy_for_sport(sport),
        source=source,
        source_url=source_url,
        season=season,
        status=status,
        sample_size=sample_size,
        metrics=metrics or {},
        notes=notes,
    )


def _fetch_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "SignalBook/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _browser_headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": referer,
        "Origin": urllib.parse.urlparse(referer).scheme + "://" + urllib.parse.urlparse(referer).netloc,
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
    }


def _nba_url(measure_type: str, season: str) -> str:
    params = {
        "Conference": "",
        "DateFrom": "",
        "DateTo": "",
        "Division": "",
        "GameSegment": "",
        "LastNGames": "0",
        "LeagueID": "00",
        "Location": "",
        "MeasureType": measure_type,
        "Month": "0",
        "OpponentTeamID": "0",
        "Outcome": "",
        "PORound": "0",
        "PaceAdjust": "N",
        "PerMode": "PerGame",
        "Period": "0",
        "PlayerExperience": "",
        "PlayerPosition": "",
        "PlusMinus": "N",
        "Rank": "N",
        "Season": season,
        "SeasonSegment": "",
        "SeasonType": "Regular Season",
        "ShotClockRange": "",
        "StarterBench": "",
        "TeamID": "0",
        "TwoWay": "0",
        "VsConference": "",
        "VsDivision": "",
    }
    return f"{NBA_API_URL}?{urllib.parse.urlencode(params)}"


def _nba_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    result_sets = data.get("resultSets") or []
    if not result_sets:
        return []
    headers = result_sets[0].get("headers", [])
    rows = result_sets[0].get("rowSet", [])
    return [dict(zip(headers, row)) for row in rows]


def _weighted_per_game(rows: list[dict[str, Any]], total_key: str, per_game_key: str) -> float:
    total = sum(_number(row.get(total_key)) for row in rows)
    games = sum(_number(row.get("gamesPlayed")) for row in rows)
    if total and games:
        return total / games
    return _weighted_average(rows, per_game_key, "gamesPlayed")


def _weighted_average(rows: list[dict[str, Any]], value_key: str, weight_key: str) -> float:
    values = []
    weights = []
    for row in rows:
        value = _number(row.get(value_key))
        weight = 1.0 if weight_key == "TEAM_COUNT" else _number(row.get(weight_key))
        if weight > 0:
            values.append(value)
            weights.append(weight)
    total_weight = sum(weights)
    if not values or total_weight <= 0:
        return 0.0
    return sum(value * weight for value, weight in zip(values, weights)) / total_weight


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _nhl_season_id(season: str | None) -> str:
    if season:
        cleaned = "".join(char for char in season if char.isdigit())
        if len(cleaned) == 8:
            return cleaned
        if len(cleaned) == 4:
            start = int(cleaned)
            return f"{start}{start + 1}"
    today = datetime.now(UTC)
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}{start + 1}"


def _basketball_season_label(season: str | None) -> str:
    if season:
        if "-" in season:
            return season
        cleaned = "".join(char for char in season if char.isdigit())
        if len(cleaned) == 8:
            return f"{cleaned[:4]}-{cleaned[-2:]}"
        if len(cleaned) == 4:
            return f"{cleaned}-{str(int(cleaned) + 1)[-2:]}"
    today = datetime.now(UTC)
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _last_completed_baseball_season() -> str:
    today = datetime.now(UTC)
    season = today.year if today.month >= 11 else today.year - 1
    return str(season)


def _source_name(sport: str) -> str:
    return {
        SPORT_SOCCER: "Football-Data local import",
        SPORT_HOCKEY: "NHL team summary",
        SPORT_BASKETBALL: "NBA team stats",
        SPORT_BASEBALL: "MLB Stats API team hitting",
        SPORT_TENNIS: "ATP Stats public page",
    }.get(sport, "unknown")


def _source_url(sport: str) -> str:
    return {
        SPORT_SOCCER: FOOTBALL_DATA_URL,
        SPORT_HOCKEY: NHL_SOURCE_URL,
        SPORT_BASKETBALL: NBA_SOURCE_URL,
        SPORT_BASEBALL: MLB_SOURCE_URL,
        SPORT_TENNIS: ATP_SOURCE_URL,
    }.get(sport, "")
