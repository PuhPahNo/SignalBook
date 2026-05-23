from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quicklin_soccer.storage import QuicklinStore


def summary_payload(db_path: Path, sport: str | None = None) -> dict[str, Any]:
    with QuicklinStore(db_path) as store:
        summary = store.report_summary(sport)
        latest_run = store.conn.execute(
            "SELECT * FROM run_metadata" + _where_sport(sport) + " ORDER BY id DESC LIMIT 1",
            _sport_params(sport),
        ).fetchone()
        latest_health = store.conn.execute(
            "SELECT * FROM provider_health" + _where_sport(sport) + " ORDER BY checked_at DESC LIMIT 1",
            _sport_params(sport),
        ).fetchone()
        open_profit = store.conn.execute(
            """
            SELECT COALESCE(SUM(settlements.payout_units), 0) AS profit
            FROM settlements
            JOIN matches ON matches.id = settlements.match_id
            """ + _where_sport(sport, "matches"),
            _sport_params(sport),
        ).fetchone()
    return {
        "summary": summary,
        "sport": sport,
        "latest_run": dict(latest_run) if latest_run else None,
        "latest_provider_health": dict(latest_health) if latest_health else None,
        "settled_profit_units": open_profit["profit"] if open_profit else 0,
        "interpretation": interpret_state(summary, latest_run, latest_health),
    }


def query_payload(db_path: Path, query: str, sport: str | None = None) -> dict[str, Any]:
    with QuicklinStore(db_path) as store:
        rows = [dict(row) for row in store.conn.execute(query, _sport_params(sport))]
    for row in rows:
        for key, value in list(row.items()):
            if key.endswith("_json") and isinstance(value, str):
                row[key] = parse_json(value)
    return {"rows": rows}


def interpret_state(summary: dict[str, Any], latest_run, latest_health) -> str:
    open_signals = int(summary.get("open_signals", 0))
    signals_total = int(summary.get("signals", 0))
    if open_signals:
        return f"{open_signals} open candidate signal(s) are on the board."
    if signals_total:
        return f"{signals_total} historical candidate signal(s) are stored, but none are open."
    if latest_run is None:
        return "No scans have run yet."
    run_summary = parse_json(latest_run["summary_json"])
    matches = int(run_summary.get("matches", 0))
    snapshots = int(run_summary.get("snapshots", 0))
    signals = int(run_summary.get("signals", 0))
    skips = int(run_summary.get("skips", 0))
    errors = int(run_summary.get("errors", 0))
    health_message = latest_health["message"] if latest_health else "provider status unknown"
    if matches == 0:
        return f"No usable live match refs were found by the provider. Last provider note: {health_message}."
    if snapshots == 0 and skips > 0:
        return "Live refs were found, but no match had usable stat snapshots."
    if signals == 0 and snapshots > 0:
        return "Usable live matches were scanned, but none cleared the EV threshold."
    if errors:
        return f"Scan completed with {errors} provider or parser errors."
    return f"{signals} signal(s) were flagged from {snapshots} usable snapshot(s)."


def signals_query(sport: str | None = None) -> str:
    return """
        SELECT value_signals.id, value_signals.created_at, value_signals.sport,
               matches.home_team || ' vs ' || matches.away_team AS match,
               matches.league, value_signals.strategy_version, value_signals.side,
               value_signals.line, value_signals.offered_odds, value_signals.fair_probability,
               value_signals.fair_odds, value_signals.ev, value_signals.stake_units,
               value_signals.expected_goals_remaining, value_signals.confidence,
               value_signals.status, matches.url
        FROM value_signals
        JOIN matches ON matches.id = value_signals.match_id
        """ + _where_sport(sport, "value_signals") + """
        ORDER BY value_signals.created_at DESC
        LIMIT 100
    """


def snapshots_query(sport: str | None = None) -> str:
    return """
        SELECT live_snapshots.id, live_snapshots.captured_at, live_snapshots.sport,
               matches.home_team || ' vs ' || matches.away_team AS match,
               matches.league, live_snapshots.phase, live_snapshots.clock, live_snapshots.minute,
               live_snapshots.home_score || '-' || live_snapshots.away_score AS score,
               live_snapshots.home_shots_on_target + live_snapshots.away_shots_on_target AS shots_on_target,
               live_snapshots.home_shots_off_target + live_snapshots.away_shots_off_target AS shots_off_target,
               live_snapshots.home_dangerous_attacks + live_snapshots.away_dangerous_attacks AS dangerous_attacks,
               live_snapshots.home_corners + live_snapshots.away_corners AS corners
        FROM live_snapshots
        JOIN matches ON matches.id = live_snapshots.match_id
        """ + _where_sport(sport, "live_snapshots") + """
        ORDER BY live_snapshots.captured_at DESC
        LIMIT 100
    """


def skips_query(sport: str | None = None) -> str:
    return """
        SELECT skipped_candidates.id, skipped_candidates.created_at, skipped_candidates.sport,
               COALESCE(matches.home_team || ' vs ' || matches.away_team, 'Unknown') AS match,
               skipped_candidates.strategy_version, skipped_candidates.reason,
               skipped_candidates.details_json
        FROM skipped_candidates
        LEFT JOIN matches ON matches.id = skipped_candidates.match_id
        """ + _where_sport(sport, "skipped_candidates") + """
        ORDER BY skipped_candidates.created_at DESC
        LIMIT 100
    """


def settlements_query(sport: str | None = None) -> str:
    return """
        SELECT settlements.id, settlements.settled_at, matches.sport,
               matches.home_team || ' vs ' || matches.away_team AS match,
               value_signals.side, value_signals.line, value_signals.offered_odds,
               settlements.final_home_score || '-' || settlements.final_away_score AS final_score,
               settlements.result, settlements.payout_units
        FROM settlements
        JOIN value_signals ON value_signals.id = settlements.signal_id
        JOIN matches ON matches.id = settlements.match_id
        """ + _where_sport(sport, "matches") + """
        ORDER BY settlements.settled_at DESC
        LIMIT 100
    """


def performance_query(sport: str | None = None) -> str:
    return """
        SELECT value_signals.sport, value_signals.strategy_version,
               COUNT(value_signals.id) AS signals,
               SUM(CASE WHEN value_signals.status = 'open' THEN 1 ELSE 0 END) AS open_signals,
               COUNT(settlements.id) AS settled,
               SUM(CASE WHEN settlements.payout_units > 0 THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN settlements.result = 'push' THEN 1 ELSE 0 END) AS pushes,
               SUM(CASE WHEN settlements.payout_units < 0 THEN 1 ELSE 0 END) AS losses,
               CASE WHEN COUNT(settlements.id) = 0 THEN NULL
                    ELSE ROUND(
                        SUM(CASE WHEN settlements.payout_units > 0 THEN 1.0 ELSE 0 END) / COUNT(settlements.id),
                        4
                    )
               END AS hit_rate,
               ROUND(COALESCE(SUM(settlements.payout_units), 0), 4) AS profit_units,
               ROUND(COALESCE(SUM(CASE WHEN settlements.id IS NOT NULL THEN value_signals.stake_units ELSE 0 END), 0), 4) AS settled_stake_units,
               CASE WHEN SUM(CASE WHEN settlements.id IS NOT NULL THEN value_signals.stake_units ELSE 0 END) = 0 THEN NULL
                    ELSE ROUND(
                        SUM(settlements.payout_units) / SUM(CASE WHEN settlements.id IS NOT NULL THEN value_signals.stake_units ELSE 0 END),
                        4
                    )
               END AS roi,
               ROUND(AVG(value_signals.ev), 4) AS avg_model_ev,
               ROUND(AVG(value_signals.confidence), 4) AS avg_confidence
        FROM value_signals
        LEFT JOIN settlements ON settlements.signal_id = value_signals.id
        """ + _where_sport(sport, "value_signals") + """
        GROUP BY value_signals.sport, value_signals.strategy_version
        ORDER BY settled DESC, signals DESC, value_signals.sport
    """


def provider_health_query(sport: str | None = None) -> str:
    return """
        SELECT sport, provider, checked_at, ok, message, latency_ms
        FROM provider_health
        """ + _where_sport(sport) + """
        ORDER BY checked_at DESC
        LIMIT 100
    """


def calibrations_query(sport: str | None = None) -> str:
    return """
        SELECT sport, strategy_version, source, source_url, season, status,
               sample_size, metrics_json, notes, created_at
        FROM calibration_runs
        """ + _where_sport(sport) + """
        ORDER BY id DESC
        LIMIT 100
    """


def runs_query(sport: str | None = None) -> str:
    return """
        SELECT id, sport, run_type, started_at, finished_at, status, summary_json
        FROM run_metadata
        """ + _where_sport(sport) + """
        ORDER BY id DESC
        LIMIT 50
    """


def _where_sport(sport: str | None, table: str | None = None) -> str:
    if not sport:
        return ""
    column = f"{table}.sport" if table else "sport"
    return f" WHERE {column} = ? "


def _sport_params(sport: str | None) -> tuple[str, ...]:
    return (sport,) if sport else ()


def parse_json(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}
