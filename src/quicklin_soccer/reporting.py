from __future__ import annotations

import csv
import html
from pathlib import Path

from quicklin_soccer.storage import QuicklinStore


def write_reports(store: QuicklinStore, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    signals_csv = output_dir / "signals.csv"
    settlements_csv = output_dir / "settlements.csv"
    html_report = output_dir / "index.html"

    _write_query_csv(
        store,
        signals_csv,
        """
        SELECT
            value_signals.id, value_signals.created_at, value_signals.sport,
            matches.home_team || ' vs ' || matches.away_team AS match,
            value_signals.strategy_version, value_signals.side, value_signals.line,
            value_signals.offered_odds, value_signals.fair_probability, value_signals.fair_odds,
            value_signals.ev, value_signals.stake_units, value_signals.confidence,
            value_signals.status, matches.url
        FROM value_signals
        JOIN matches ON matches.id = value_signals.match_id
        ORDER BY value_signals.created_at DESC
        """,
    )
    _write_query_csv(
        store,
        settlements_csv,
        """
        SELECT
            settlements.id, settlements.settled_at, matches.sport,
            matches.home_team || ' vs ' || matches.away_team AS match,
            value_signals.side, value_signals.line, value_signals.offered_odds,
            settlements.final_home_score, settlements.final_away_score,
            settlements.result, settlements.payout_units
        FROM settlements
        JOIN value_signals ON value_signals.id = settlements.signal_id
        JOIN matches ON matches.id = settlements.match_id
        ORDER BY settlements.settled_at DESC
        """,
    )
    html_report.write_text(_render_html(store), encoding="utf-8")
    return {"html": html_report, "signals_csv": signals_csv, "settlements_csv": settlements_csv}


def _write_query_csv(store: QuicklinStore, path: Path, query: str) -> None:
    rows = list(store.conn.execute(query))
    with path.open("w", newline="", encoding="utf-8") as file:
        if not rows:
            file.write("")
            return
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)


def _render_html(store: QuicklinStore) -> str:
    summary = store.report_summary()
    strategy_rows = list(
        store.conn.execute(
            """
            SELECT sport, strategy_version, COUNT(*) AS signals, SUM(ev) AS total_ev,
                   SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_signals
            FROM value_signals
            GROUP BY sport, strategy_version
            ORDER BY signals DESC
            """
        )
    )
    sport_rows = list(
        store.conn.execute(
            """
            SELECT matches.sport, COUNT(DISTINCT matches.id) AS matches,
                   COUNT(value_signals.id) AS signals, AVG(value_signals.ev) AS avg_ev
            FROM matches
            LEFT JOIN value_signals ON value_signals.match_id = matches.id
            GROUP BY matches.sport
            ORDER BY matches.sport
            """
        )
    )
    league_rows = list(
        store.conn.execute(
            """
            SELECT matches.sport, COALESCE(matches.league, 'Unknown') AS league, COUNT(value_signals.id) AS signals,
                   AVG(value_signals.ev) AS avg_ev
            FROM value_signals
            JOIN matches ON matches.id = value_signals.match_id
            GROUP BY matches.sport, COALESCE(matches.league, 'Unknown')
            ORDER BY signals DESC
            LIMIT 25
            """
        )
    )
    provider_rows = list(
        store.conn.execute(
            """
            SELECT sport, provider, checked_at, ok, message, latency_ms
            FROM provider_health
            ORDER BY checked_at DESC
            LIMIT 20
            """
        )
    )
    performance_rows = list(
        store.conn.execute(
            """
            SELECT value_signals.sport, value_signals.strategy_version,
                   COUNT(value_signals.id) AS signals,
                   SUM(CASE WHEN value_signals.status = 'open' THEN 1 ELSE 0 END) AS open_signals,
                   COUNT(settlements.id) AS settled,
                   SUM(CASE WHEN settlements.payout_units > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN settlements.result = 'push' THEN 1 ELSE 0 END) AS pushes,
                   SUM(CASE WHEN settlements.payout_units < 0 THEN 1 ELSE 0 END) AS losses,
                   ROUND(COALESCE(SUM(settlements.payout_units), 0), 4) AS profit_units
            FROM value_signals
            LEFT JOIN settlements ON settlements.signal_id = value_signals.id
            GROUP BY value_signals.sport, value_signals.strategy_version
            ORDER BY settled DESC, signals DESC, value_signals.sport
            """
        )
    )
    settlement = store.conn.execute(
        """
        SELECT COUNT(*) AS settled, COALESCE(SUM(payout_units), 0) AS profit
        FROM settlements
        """
    ).fetchone()

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>SignalBook Multi-Sport Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; color: #172026; }}
    h1, h2 {{ margin-bottom: 8px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0 28px; }}
    th, td {{ border: 1px solid #d9e0e6; padding: 8px; text-align: left; }}
    th {{ background: #f4f7f9; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }}
    .card {{ border: 1px solid #d9e0e6; padding: 12px; border-radius: 6px; }}
    .value {{ font-size: 24px; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>SignalBook Multi-Sport Report</h1>
  <p>Local research output. Signals are candidates, not automatic bets.</p>
  <div class="cards">
    {_summary_card("Matches", summary["matches"])}
    {_summary_card("Snapshots", summary["snapshots"])}
    {_summary_card("Signals", summary["signals"])}
    {_summary_card("Open", summary["open_signals"])}
    {_summary_card("Settled", settlement["settled"])}
    {_summary_card("Profit Units", round(settlement["profit"], 4))}
  </div>
  <h2>Strategy Versions</h2>
  {_table(strategy_rows)}
  <h2>Sport Breakdown</h2>
  {_table(sport_rows)}
  <h2>League Breakdown</h2>
  {_table(league_rows)}
  <h2>Settled Performance</h2>
  {_table(performance_rows)}
  <h2>Provider Health</h2>
  {_table(provider_rows)}
  <h2>Exports</h2>
  <p>See <code>signals.csv</code> and <code>settlements.csv</code> in this folder.</p>
</body>
</html>
"""


def _summary_card(label: str, value) -> str:
    return f"<div class='card'><div>{html.escape(str(label))}</div><div class='value'>{html.escape(str(value))}</div></div>"


def _table(rows) -> str:
    if not rows:
        return "<p>No rows yet.</p>"
    headers = rows[0].keys()
    head = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{html.escape(str(row[header]))}</td>" for header in headers) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
