from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


# Path to the repo-root signalbook.py entrypoint, used when the bot needs
# to spawn an out-of-process settle pass so it doesn't block scanning.
_SIGNALBOOK_PY = Path(__file__).resolve().parents[2] / "signalbook.py"

import dataclasses

from quicklin_soccer.aiscore import BrowserConfig, StatsUnavailable, classify_error
from quicklin_soccer.calibration import run_calibrations
from quicklin_soccer.football_data import backtest_historical_baseline, import_football_data_csv
from quicklin_soccer.models import (
    BetSignal,
    MatchRef,
    OddsQuote,
    ProviderHealth,
    Settlement,
    ValueSignal,
    is_coherent_two_sided_odds,
    now_iso,
)
from quicklin_soccer.odds_estimator import (
    MIN_CONFIDENCE as ESTIMATOR_MIN_CONFIDENCE,
    EstimatedOdds,
    OddsEstimator,
    estimated_odds_to_quotes,
)
from quicklin_soccer import pinnacle
from quicklin_soccer.pricing import settle_total_bet
from quicklin_soccer.providers import AiScoreProvider
from quicklin_soccer.reporting import write_reports
from quicklin_soccer.sports import SPORT_SOCCER, SUPPORTED_SPORTS, default_strategy_for_sport
from quicklin_soccer.storage import DEFAULT_DB_PATH, QuicklinStore
from quicklin_soccer.strategy import (
    DEFAULT_STRATEGY_VERSION,
    LEGACY_STRATEGY_VERSION,
    SPORT_STRATEGY_VERSIONS,
    StrategyConfig,
    evaluate_ev_totals,
    evaluate_match,
    evaluate_sport_totals,
)
from quicklin_soccer.webapp import run_server


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in {"-h", "--help"}):
        argv.insert(0, "scan")

    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def scan_command(args: argparse.Namespace) -> int:
    config = _browser_config(args)
    strategy = args.strategy or default_strategy_for_sport(args.sport)
    if strategy == LEGACY_STRATEGY_VERSION and args.sport != SPORT_SOCCER:
        raise SystemExit("legacy_threshold_v1 is only supported for soccer.")
    rows: list[dict[str, Any]] = []
    summary = {"sport": args.sport, "matches": 0, "snapshots": 0, "signals": 0, "duplicates": 0, "skips": 0, "errors": 0}

    with QuicklinStore(args.db) as store:
        strategy_config = _strategy_config(args, strategy, store.latest_successful_calibration(args.sport))
        run_id = store.start_run("scan", args.sport)
        try:
            with AiScoreProvider.create(config) as provider:
                provider_started = time.monotonic()
                refs = provider.list_live_matches(sport=args.sport, limit=args.limit)
                store.insert_provider_health(
                    ProviderHealth(
                        provider="aiscore",
                        checked_at=now_iso(),
                        ok=True,
                        message=f"listed {len(refs)} {args.sport} live matches",
                        latency_ms=int((time.monotonic() - provider_started) * 1000),
                        sport=args.sport,
                    )
                )
                _print(args, f"Found {len(refs)} live match refs")
                pinnacle_client, pinnacle_games = _pinnacle_setup(args)
                for index, ref in enumerate(refs, start=1):
                    _print(args, f"[{index}/{len(refs)}] {ref.home_team} vs {ref.away_team}")
                    match_id = store.upsert_match(ref)
                    try:
                        snapshot, odds_quotes = provider.get_live_snapshot(ref)
                        # Prefer Pinnacle's live sharp total; AiScore odds are a
                        # frozen pre-match line. Keep AiScore odds only as a
                        # fallback when Pinnacle has no match for this game.
                        pinnacle_quotes = _pinnacle_odds_for(
                            snapshot, ref, pinnacle_client, pinnacle_games, args.sport
                        )
                        if pinnacle_quotes:
                            odds_quotes = pinnacle_quotes
                        snapshot_id = store.insert_snapshot(match_id, snapshot)
                        odds_ids = {
                            quote.side: store.insert_odds_quote(match_id, quote)
                            for quote in odds_quotes
                        }
                        summary["snapshots"] += 1
                        history = None
                        if args.sport == SPORT_SOCCER:
                            try:
                                history = provider.historical_profile(ref)
                            except Exception as exc:
                                store.insert_skip(
                                    "aiscore",
                                    strategy,
                                    "history_unavailable",
                                    {"url": ref.url, "error": classify_error(exc)},
                                    match_id,
                                    snapshot_id,
                                    args.sport,
                                )

                        if strategy == LEGACY_STRATEGY_VERSION:
                            if history is None:
                                _skip(store, args, match_id, snapshot_id, "legacy_history_unavailable", {})
                                summary["skips"] += 1
                                continue
                            signal = _legacy_signal(provider, ref, history)
                            if signal is None:
                                _skip(store, args, match_id, snapshot_id, "legacy_no_signal", {})
                                summary["skips"] += 1
                                continue
                            converted = _legacy_to_value_signal(signal, ref, odds_quotes, strategy_config)
                            converted_odds_source = "market"
                            converted_odds_confidence = None
                            estimated_quote_side_to_id: dict[str, int] = {}
                            if converted is None:
                                # Bookmaker didn't post the two-sided total. Try
                                # the estimator before giving up.
                                estimate_result = _try_estimate_total_odds(
                                    store,
                                    match_id,
                                    snapshot,
                                    args.sport,
                                )
                                if estimate_result is None:
                                    _skip(
                                        store, args, match_id, snapshot_id,
                                        "legacy_missing_odds",
                                        {"estimator": "not_attempted_or_below_floor"},
                                    )
                                    summary["skips"] += 1
                                    continue
                                synthetic_quotes, estimated_quote_side_to_id, estimated = estimate_result
                                converted = _legacy_to_value_signal(
                                    signal, ref, synthetic_quotes, strategy_config
                                )
                                if converted is None:
                                    _skip(
                                        store, args, match_id, snapshot_id,
                                        "legacy_missing_odds",
                                        {"estimator": "side_unavailable"},
                                    )
                                    summary["skips"] += 1
                                    continue
                                converted_odds_source = f"estimated_{estimated.method}"
                                converted_odds_confidence = estimated.confidence
                            if converted_odds_source != "market":
                                converted = dataclasses.replace(
                                    converted,
                                    odds_source=converted_odds_source,
                                    odds_confidence=converted_odds_confidence,
                                    reason_codes=(*converted.reason_codes, converted_odds_source),
                                )
                            signal_quote_id = (
                                estimated_quote_side_to_id.get(converted.side)
                                if converted_odds_source != "market"
                                else odds_ids.get(converted.side)
                            )
                            signal_id, is_new = _insert_signal_if_new(
                                store,
                                match_id,
                                snapshot_id,
                                None,
                                signal_quote_id,
                                converted,
                            )
                            if not is_new:
                                summary["duplicates"] += 1
                                _print(args, f"  holding open signal #{signal_id}: {converted.side} {converted.line}")
                                continue
                            rows.append({"id": signal_id, **converted.as_row()})
                            _print_signal(args, converted, signal_id)
                            summary["signals"] += 1
                            continue

                        if args.sport == SPORT_SOCCER:
                            evaluation = evaluate_ev_totals(snapshot, odds_quotes, history, strategy_config)
                        else:
                            evaluation = evaluate_sport_totals(snapshot, odds_quotes, strategy_config)

                        # If the strategy bailed only because the bookmaker
                        # didn't post a two-sided total, ask the estimator
                        # for a synthetic market and re-evaluate against it.
                        estimator_odds_ids: dict[str, int] = {}
                        estimator_metadata: EstimatedOdds | None = None
                        if (
                            evaluation.skip_reason == "missing_two_sided_total_odds"
                            and not evaluation.signals
                        ):
                            estimate_result = _try_estimate_total_odds(
                                store, match_id, snapshot, args.sport,
                            )
                            if estimate_result is not None:
                                synthetic_quotes, estimator_odds_ids, estimator_metadata = estimate_result
                                combined_quotes = (*odds_quotes, *synthetic_quotes)
                                if args.sport == SPORT_SOCCER:
                                    evaluation = evaluate_ev_totals(
                                        snapshot, combined_quotes, history, strategy_config
                                    )
                                else:
                                    evaluation = evaluate_sport_totals(
                                        snapshot, combined_quotes, strategy_config
                                    )

                        prediction_id = None
                        if evaluation.prediction:
                            prediction_id = store.insert_prediction(match_id, snapshot_id, evaluation.prediction)

                        if not evaluation.signals:
                            skip_details = dict(evaluation.details or {})
                            if estimator_metadata is not None:
                                skip_details["estimator_method"] = estimator_metadata.method
                                skip_details["estimator_confidence"] = estimator_metadata.confidence
                            _skip(store, args, match_id, snapshot_id, evaluation.skip_reason or "no_signal", skip_details)
                            summary["skips"] += 1
                            continue

                        for signal in evaluation.signals:
                            if estimator_metadata is not None:
                                # Tag the synthesized side(s) so the dashboard
                                # and PnL analytics can split estimated vs
                                # market-priced trades.
                                signal = dataclasses.replace(
                                    signal,
                                    odds_source=f"estimated_{estimator_metadata.method}",
                                    odds_confidence=estimator_metadata.confidence,
                                    reason_codes=(*signal.reason_codes, f"estimated_{estimator_metadata.method}"),
                                )
                                quote_id_for_signal = estimator_odds_ids.get(signal.side, odds_ids.get(signal.side))
                            else:
                                quote_id_for_signal = odds_ids.get(signal.side)
                            signal_id, is_new = _insert_signal_if_new(
                                store,
                                match_id,
                                snapshot_id,
                                prediction_id,
                                quote_id_for_signal,
                                signal,
                            )
                            if not is_new:
                                summary["duplicates"] += 1
                                _print(args, f"  holding open signal #{signal_id}: {signal.side} {signal.line}")
                                continue
                            rows.append({"id": signal_id, **signal.as_row()})
                            _print_signal(args, signal, signal_id)
                            summary["signals"] += 1

                    except StatsUnavailable as exc:
                        _skip(store, args, match_id, None, str(exc), {})
                        summary["skips"] += 1
                    except Exception as exc:
                        summary["errors"] += 1
                        message = classify_error(exc)
                        store.insert_skip("aiscore", strategy, "provider_error", {"url": ref.url, "error": message}, match_id, sport=args.sport)
                        _print(args, f"  error: {message}")
                    finally:
                        summary["matches"] += 1

            if args.output:
                _write_csv(args.output, rows)
            store.finish_run(run_id, "ok" if summary["errors"] == 0 else "partial", summary)
        except Exception:
            store.finish_run(run_id, "failed", summary)
            raise

    if args.json:
        print(json.dumps({**summary, "signal_count": summary["signals"], "signals": rows}, indent=2))
    else:
        print(
            f"Done. Matches {summary['matches']}, snapshots {summary['snapshots']}, "
            f"signals {summary['signals']}, duplicates {summary['duplicates']}, "
            f"skips {summary['skips']}, errors {summary['errors']}."
        )
        if args.output:
            print(f"Wrote {args.output}")
    return 0 if summary["errors"] == 0 else 1


def _insert_signal_if_new(
    store: QuicklinStore,
    match_id: int,
    snapshot_id: int | None,
    prediction_id: int | None,
    odds_quote_id: int | None,
    signal: ValueSignal,
) -> tuple[int, bool]:
    """Emit a new paper trade if there isn't already an OPEN one for this
    match+sport+strategy+side. Otherwise log the would-have-emitted line
    and odds to line_movements (for post-hoc EV analysis) and return the
    existing signal's id. Estimated signals route through the same path,
    so the dedup behavior applies identically to market and estimated."""
    existing_id = store.emitted_signal_id(match_id, signal)
    if existing_id is not None:
        try:
            store.insert_line_movement(existing_id, match_id, signal)
        except Exception:
            # Line-movement logging is best-effort; never fail the scan
            # just because the analytics insert had trouble.
            pass
        return existing_id, False
    return store.insert_signal(match_id, snapshot_id, prediction_id, odds_quote_id, signal), True


def monitor_command(args: argparse.Namespace) -> int:
    loops = 0
    while args.loops is None or loops < args.loops:
        loops += 1
        scan_args = argparse.Namespace(**vars(args))
        scan_args.func = scan_command
        scan_args.output = None
        exit_code = scan_command(scan_args)
        if exit_code != 0 and args.stop_on_error:
            return exit_code
        if args.loops is not None and loops >= args.loops:
            break
        time.sleep(args.interval_seconds)
    return 0


def bot_command(args: argparse.Namespace) -> int:
    sports = _bot_sports(args.sports)
    loops = 0
    last_settle_at = 0.0
    # Settle is run in a separate Python process so a long pass over hundreds
    # of open signals doesn't block the scan loop. We only ever keep one
    # settle process alive at a time; if the previous one hasn't finished
    # by the time the next settle window opens, we skip and try again later.
    settle_proc: subprocess.Popen | None = None
    print(
        "SignalBook bot running: "
        f"scanning {', '.join(sports)} every {args.scan_interval_seconds}s; "
        f"settling every {args.settle_interval_seconds}s "
        f"(settle runs in a background subprocess so it does not block scans)."
    )
    while args.loops is None or loops < args.loops:
        loops += 1
        loop_started = time.monotonic()
        print(f"\nBot loop {loops} starting...")
        for sport in sports:
            scan_args = _bot_scan_args(args, sport)
            try:
                exit_code = scan_command(scan_args)
                if exit_code != 0:
                    print(f"{sport}: scan finished with warnings.")
            except Exception as exc:
                print(f"{sport}: scan failed but bot is continuing: {classify_error(exc)}")

        # Reap a finished settle subprocess so we can launch a fresh one.
        if settle_proc is not None and settle_proc.poll() is not None:
            print(
                f"settle subprocess pid={settle_proc.pid} finished "
                f"with exit code {settle_proc.returncode}"
            )
            settle_proc = None

        now = time.monotonic()
        if now - last_settle_at >= args.settle_interval_seconds:
            if settle_proc is not None:
                print(
                    f"settle subprocess pid={settle_proc.pid} is still running; "
                    "deferring next settle pass."
                )
            else:
                settle_proc = _spawn_settle_subprocess(args)
                if settle_proc is not None:
                    last_settle_at = time.monotonic()

        if args.loops is not None and loops >= args.loops:
            break
        elapsed = time.monotonic() - loop_started
        time.sleep(max(0, args.scan_interval_seconds - elapsed))

    # On bot shutdown, wait for any lingering settle subprocess so we don't
    # leave an orphaned Chrome instance running on Anthony's desktop.
    if settle_proc is not None and settle_proc.poll() is None:
        print(f"waiting for settle subprocess pid={settle_proc.pid} to finish...")
        try:
            settle_proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            print("settle subprocess did not finish in 120s; terminating.")
            settle_proc.terminate()
    return 0


def _spawn_settle_subprocess(args: argparse.Namespace) -> subprocess.Popen | None:
    """Spawn `signalbook.py settle` as an independent process so its
    Chrome driver is isolated from the scanner's and SQLite writes don't
    contend in the same connection."""
    cmd = [sys.executable, str(_SIGNALBOOK_PY), "settle", "--db", str(args.db)]
    if getattr(args, "headless", False):
        cmd.append("--headless")
    try:
        proc = subprocess.Popen(cmd)
        print(f"settle subprocess pid={proc.pid} started ({' '.join(cmd)})")
        return proc
    except Exception as exc:
        print(f"failed to start settle subprocess: {classify_error(exc)}")
        return None


# New `status` value used by dedupe-historical. The settle flow filters on
# status='open', so superseded rows are inert — they will not be re-fetched
# from AIScore and will not produce new settlement rows.
SUPERSEDED_STATUS = "superseded"


def dedupe_historical_command(args: argparse.Namespace) -> int:
    """One-shot cleanup. For each (match_id, sport, strategy_version, side)
    group with more than one value_signal row, keep the EARLIEST (lowest id)
    and mark every subsequent row 'superseded'. Default behavior is a
    dry-run that prints the summary; pass --apply to actually mutate.

    Anthony's win/loss tallies were double-counting matches because the
    pre-fix dedup keyed on line. This script collapses the history to one
    paper trade per (match, side, strategy) — matching the new live
    behavior — so historical PnL stops over-counting."""
    import sqlite3

    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run are mutually exclusive.")
    dry_run = not args.apply  # default is dry-run

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        # Survey first: how many rows total, how many groups have dupes,
        # how many would be marked superseded.
        groups = list(conn.execute("""
            SELECT match_id, sport, strategy_version, side,
                   COUNT(*) AS n_rows,
                   MIN(id) AS keep_id,
                   GROUP_CONCAT(id) AS all_ids,
                   GROUP_CONCAT(DISTINCT status) AS statuses
            FROM value_signals
            GROUP BY match_id, sport, strategy_version, side
            HAVING n_rows > 1
            ORDER BY n_rows DESC
        """))
        total_signals_before = conn.execute("SELECT COUNT(*) FROM value_signals").fetchone()[0]
        affected_matches = len({(g["match_id"], g["sport"]) for g in groups})
        rows_to_supersede = sum(g["n_rows"] - 1 for g in groups)
        rows_that_remain = total_signals_before - rows_to_supersede

        # Per-sport breakdown.
        per_sport: dict[str, dict[str, int]] = {}
        for g in groups:
            d = per_sport.setdefault(g["sport"], {"groups": 0, "supersede": 0})
            d["groups"] += 1
            d["supersede"] += g["n_rows"] - 1

        mode = "DRY-RUN" if dry_run else "APPLY"
        print(f"[{mode}] dedupe-historical against {args.db}")
        print(f"  before: {total_signals_before} total value_signals")
        print(f"  dup groups (more than one row per match+sport+strategy+side): {len(groups)}")
        print(f"  affected matches: {affected_matches}")
        print(f"  rows to mark '{SUPERSEDED_STATUS}': {rows_to_supersede}")
        print(f"  rows that will remain: {rows_that_remain}")
        print("  per sport:")
        for sport in sorted(per_sport):
            d = per_sport[sport]
            print(f"    {sport:11} dup_groups={d['groups']:>4}  supersede_rows={d['supersede']:>5}")

        # Show top 5 worst offenders so Anthony can sanity check.
        if groups:
            print("\n  worst-offender groups (top 5 by row count):")
            for g in groups[:5]:
                ids = sorted(int(i) for i in g["all_ids"].split(",") if i)
                keep = int(g["keep_id"])
                supersede = [str(i) for i in ids if i != keep]
                shown = supersede[:5]
                tail = "..." if len(supersede) > len(shown) else ""
                print(f"    match={g['match_id']} {g['sport']:8} strat={g['strategy_version']:25} "
                      f"side={g['side']:5} n={g['n_rows']} keep_id={keep} "
                      f"supersede_ids={','.join(shown)}{tail} statuses={g['statuses']}")

        if dry_run:
            print(f"\n[{mode}] no changes written. Re-run with --apply to actually mutate.")
            return 0

        # Apply: mark every non-earliest row 'superseded'.
        updated = 0
        with conn:
            for g in groups:
                cur = conn.execute(
                    """
                    UPDATE value_signals
                    SET status = ?
                    WHERE match_id = ?
                      AND sport = ?
                      AND strategy_version = ?
                      AND side = ?
                      AND id != ?
                    """,
                    (SUPERSEDED_STATUS, g["match_id"], g["sport"],
                     g["strategy_version"], g["side"], g["keep_id"]),
                )
                updated += cur.rowcount
        print(f"\n[{mode}] complete. {updated} rows marked '{SUPERSEDED_STATUS}'.")
        # Verify net count.
        after = conn.execute(
            "SELECT COUNT(*) FROM value_signals WHERE status != ?",
            (SUPERSEDED_STATUS,),
        ).fetchone()[0]
        print(f"  rows now NOT superseded: {after}")
        return 0
    finally:
        conn.close()


def settle_command(args: argparse.Namespace) -> int:
    settled = 0
    skipped = 0
    errors = 0
    skip_reasons: dict[str, int] = {}
    captured = 0
    capture_dir = getattr(args, "capture_dir", None)
    with QuicklinStore(args.db) as store:
        open_signals = store.open_signals()
        with AiScoreProvider.create(_browser_config(args)) as provider:
            for row in open_signals:
                ref = MatchRef(
                    provider=row["provider"],
                    provider_match_id=row["provider_match_id"],
                    url=row["url"],
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    league=row["league"],
                    start_time=row["start_time"],
                    sport=row["sport"] or row["match_sport"] or SPORT_SOCCER,
                )
                # Isolate each match: a stale/404 URL or a parse hiccup on one
                # open signal must not abort settling the rest of the backlog.
                try:
                    result = provider.settle_match(ref)
                    reason = _settlement_skip_reason(ref.sport, result)
                    # Capture the page text for matches stuck on a missing detail
                    # scoreboard — the sample we need to rebuild the parser. Done
                    # regardless of --force, since --force settles on bad scores.
                    if capture_dir is not None and reason == "no_detail_scoreboard":
                        if _capture_page_text(capture_dir, ref, result):
                            captured += 1
                    if not args.force and reason is not None:
                        skipped += 1
                        key = f"{ref.sport}:{reason}"
                        skip_reasons[key] = skip_reasons.get(key, 0) + 1
                        _print(args, f"skip open signal {row['id']} ({ref.sport}): {reason}")
                        continue
                    final_total = result["home_score"] + result["away_score"]
                    settlement_result, payout = settle_total_bet(
                        final_total,
                        row["line"],
                        row["side"],
                        row["offered_odds"],
                        row["stake_units"],
                    )
                    settlement = Settlement(
                        signal_id=row["id"],
                        match_id=row["match_id"],
                        settled_at=now_iso(),
                        final_home_score=result["home_score"],
                        final_away_score=result["away_score"],
                        result=settlement_result,
                        payout_units=payout,
                    )
                    store.insert_settlement(settlement, result.get("raw"))
                    settled += 1
                    _print(args, f"settled {row['id']}: {settlement_result} {payout:+.2f}u")
                except Exception as exc:
                    errors += 1
                    _print(args, f"error settling signal {row['id']} ({ref.sport}): {classify_error(exc)}")
                    continue

    summary: dict[str, Any] = {"settled": settled, "skipped": skipped, "errors": errors, "skip_reasons": skip_reasons}
    if capture_dir is not None:
        summary["captured_pages"] = captured
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Done. Settled {settled}, skipped {skipped}, errors {errors}.")
        if skip_reasons:
            print("Skip reasons (sport:reason):")
            for key in sorted(skip_reasons):
                print(f"  {key}: {skip_reasons[key]}")
        if capture_dir is not None:
            print(f"Captured {captured} page-text sample(s) to {capture_dir}.")
    return 0


def backtest_command(args: argparse.Namespace) -> int:
    with QuicklinStore(args.db) as store:
        imported = 0
        paths = _csv_paths(args)
        for path in paths:
            imported += import_football_data_csv(store, path, season=args.season)
        result = backtest_historical_baseline(store, min_ev=args.min_ev, min_history=args.min_history)

    output = {"imported": imported, **result}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))
    return 0


def calibrate_command(args: argparse.Namespace) -> int:
    with QuicklinStore(args.db) as store:
        results = run_calibrations(args.sport, season=args.season, store=store)
        for result in results:
            store.insert_calibration_run(result)

    payload = {"calibrations": [result.as_dict() for result in results]}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for result in results:
            print(
                f"{result.sport}: {result.status} from {result.source} "
                f"({result.sample_size} rows)"
            )
    return 0


def report_command(args: argparse.Namespace) -> int:
    with QuicklinStore(args.db) as store:
        paths = write_reports(store, args.output_dir)
    if args.json:
        print(json.dumps({key: str(path) for key, path in paths.items()}, indent=2))
    else:
        print(f"Wrote report: {paths['html']}")
        print(f"Wrote CSV: {paths['signals_csv']}")
        print(f"Wrote CSV: {paths['settlements_csv']}")
    return 0


def web_command(args: argparse.Namespace) -> int:
    run_server(host=args.host, port=args.port, db_path=args.db)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SignalBook multi-sport betting research tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Run one live AIScore scan.")
    _add_common(scan)
    scan.add_argument("--output", type=Path, help="Optional CSV output path for signals.")
    scan.set_defaults(func=scan_command)

    monitor = subparsers.add_parser("monitor", help="Repeat live scans for forward paper trading.")
    _add_common(monitor)
    monitor.add_argument("--interval-seconds", type=int, default=60)
    monitor.add_argument("--loops", type=int, help="Number of scan loops. Omit for continuous monitoring.")
    monitor.add_argument("--stop-on-error", action="store_true")
    monitor.set_defaults(func=monitor_command)

    bot = subparsers.add_parser("bot", help="Run the automatic all-sport paper-trading bot.")
    bot.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    bot.add_argument("--sports", default="all", help="Comma-separated sports list, or all.")
    bot.add_argument("--limit", type=int, default=10, help="Maximum live matches per sport per loop.")
    bot.add_argument("--scan-interval-seconds", type=int, default=60)
    bot.add_argument("--settle-interval-seconds", type=int, default=300)
    bot.add_argument("--loops", type=int, help="Number of bot loops. Omit for continuous bot mode.")
    # Headed Chrome is now the default — see scan parser comment above.
    bot.add_argument("--headless", action="store_true",
                     help="Run Chrome headless (will be detected by AIScore).")
    bot.add_argument("--headful", action="store_true",
                     help="(Kept for back-compat — headful is now the default.)")
    bot.add_argument("--include-skipped", action="store_true")
    bot.add_argument("--wait-timeout", type=int, default=25)
    bot.add_argument("--page-timeout", type=int, default=35)
    bot.add_argument("--min-ev", type=float, default=0.03)
    bot.add_argument("--stake-units", type=float, default=1.0)
    bot.add_argument("--max-odds-age-seconds", type=int, default=120)
    bot.set_defaults(func=bot_command)

    settle = subparsers.add_parser("settle", help="Settle open paper-trading signals.")
    settle.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    settle.add_argument("--force", action="store_true", help="Settle even if AIScore does not look final yet.")
    settle.add_argument("--json", action="store_true")
    settle.add_argument("--headless", action="store_true",
                        help="Run Chrome headless (will be detected by AIScore).")
    settle.add_argument("--headful", action="store_true",
                        help="(Kept for back-compat — headful is now the default.)")
    settle.add_argument("--wait-timeout", type=int, default=25)
    settle.add_argument("--page-timeout", type=int, default=35)
    settle.add_argument(
        "--capture-dir",
        type=Path,
        help="Dump raw detail-page text for matches that can't settle "
        "(non-soccer 'no_detail_scoreboard') so the tennis/baseball "
        "scoreboard parser can be rebuilt against real samples.",
    )
    settle.set_defaults(func=settle_command)

    backtest = subparsers.add_parser("backtest", help="Import Football-Data CSVs and run historical baseline.")
    backtest.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    backtest.add_argument("--csv", type=Path, action="append", default=[])
    backtest.add_argument("--csv-dir", type=Path)
    backtest.add_argument("--season")
    backtest.add_argument("--min-ev", type=float, default=0.03)
    backtest.add_argument("--min-history", type=int, default=6)
    backtest.add_argument("--output", type=Path)
    backtest.set_defaults(func=backtest_command)

    calibrate = subparsers.add_parser("calibrate", help="Refresh free-source model calibration baselines.")
    calibrate.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    calibrate.add_argument("--sport", choices=("all", *SUPPORTED_SPORTS), default="all")
    calibrate.add_argument("--season", help="Optional source season, e.g. 2025, 2025-26, or 20252026.")
    calibrate.add_argument("--json", action="store_true")
    calibrate.set_defaults(func=calibrate_command)

    report = subparsers.add_parser("report", help="Write HTML and CSV reports from SQLite.")
    report.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    report.add_argument("--output-dir", type=Path, default=Path("output/report"))
    report.add_argument("--json", action="store_true")
    report.set_defaults(func=report_command)

    web = subparsers.add_parser("web", help="Start the local dashboard web console.")
    web.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.set_defaults(func=web_command)

    dedupe = subparsers.add_parser(
        "dedupe-historical",
        help="One-shot: collapse duplicate signals per (match,sport,strategy,side) to a single 'open' or 'settled' row, marking the rest 'superseded'. Default is --dry-run.",
    )
    dedupe.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    dedupe.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate the DB. Without this flag the command runs in dry-run mode and prints what it WOULD do.",
    )
    dedupe.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit dry-run (default behavior). Mutually exclusive with --apply.",
    )
    dedupe.set_defaults(func=dedupe_historical_command)

    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--sport", choices=SUPPORTED_SPORTS, default=SPORT_SOCCER)
    parser.add_argument("--limit", type=int, help="Maximum live matches to scan.")
    # Headed Chrome is now the default — AIScore's bot check serves an
    # empty page to headless Chrome. --headless lets you opt back in.
    # --headful kept as a silent no-op for back-compat with old scripts.
    parser.add_argument("--headless", action="store_true",
                        help="Run Chrome headless (will be detected by AIScore as of late May 2026).")
    parser.add_argument("--headful", action="store_true",
                        help="(Kept for back-compat — headful is now the default.)")
    parser.add_argument("--include-skipped", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--wait-timeout", type=int, default=25)
    parser.add_argument("--page-timeout", type=int, default=35)
    parser.add_argument("--strategy", choices=SPORT_STRATEGY_VERSIONS, default=None)
    parser.add_argument("--min-ev", type=float, default=0.03)
    parser.add_argument("--stake-units", type=float, default=1.0)
    parser.add_argument("--max-odds-age-seconds", type=int, default=120)
    parser.add_argument(
        "--odds-source",
        choices=["pinnacle", "aiscore"],
        default="pinnacle",
        help="Odds source. 'pinnacle' (default) uses Pinnacle's live sharp "
        "totals; AiScore only serves a frozen pre-match line. 'aiscore' keeps "
        "the legacy behavior. Game state/stats always come from AiScore.",
    )


def _browser_config(args: argparse.Namespace) -> BrowserConfig:
    return BrowserConfig(
        headless=getattr(args, "headless", False),
        wait_timeout=args.wait_timeout,
        page_load_timeout=args.page_timeout,
    )


def _bot_sports(value: str) -> list[str]:
    requested = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not requested or requested == ["all"] or "all" in requested:
        return list(SUPPORTED_SPORTS)
    unknown = [sport for sport in requested if sport not in SUPPORTED_SPORTS]
    if unknown:
        raise SystemExit(f"Unsupported sport(s): {', '.join(unknown)}")
    return requested


def _bot_scan_args(args: argparse.Namespace, sport: str) -> argparse.Namespace:
    return argparse.Namespace(
        db=args.db,
        sport=sport,
        limit=args.limit,
        headless=getattr(args, "headless", False),
        headful=getattr(args, "headful", False),
        include_skipped=args.include_skipped,
        json=False,
        wait_timeout=args.wait_timeout,
        page_timeout=args.page_timeout,
        strategy=None,
        min_ev=args.min_ev,
        stake_units=args.stake_units,
        max_odds_age_seconds=args.max_odds_age_seconds,
        odds_source=getattr(args, "odds_source", "pinnacle"),
        output=None,
    )


def _bot_settle_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        db=args.db,
        force=False,
        json=False,
        headless=getattr(args, "headless", False),
        headful=getattr(args, "headful", False),
        wait_timeout=args.wait_timeout,
        page_timeout=args.page_timeout,
    )


def _settlement_skip_reason(sport: str, result: dict[str, Any]) -> str | None:
    """Why this match can't be settled yet, or None if it's ready.

    Returns a reason string (rather than a bare bool) so the settle pass can
    log a per-sport breakdown of *why* signals stay open. That breakdown is
    how the tennis/baseball pile-up gets diagnosed: both sports fail the
    `no_detail_scoreboard` gate because their inning/set markup never parses
    into a detail scoreboard upstream."""
    if sport != SPORT_SOCCER and result.get("score_source") != "detail_scoreboard":
        return "no_detail_scoreboard"
    if result.get("is_final"):
        return None
    if result.get("status_id") in {8, 10, 11, 12, 13}:
        return None
    if sport == SPORT_SOCCER:
        return None if int(result.get("minute") or 0) >= 90 else "soccer_under_90"
    return "not_final"


def _is_settlement_ready(sport: str, result: dict[str, Any]) -> bool:
    return _settlement_skip_reason(sport, result) is None


def _capture_page_text(capture_dir: Path, ref: MatchRef, result: dict[str, Any]) -> bool:
    """Dump the raw detail-page text for a match we couldn't settle, so the
    tennis/baseball scoreboard parser can be rebuilt against a real sample.
    Returns True if a file was written."""
    text = result.get("page_text")
    if not text:
        return False
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{ref.sport}_{ref.provider_match_id}")[:120]
    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
        (capture_dir / f"{slug}.txt").write_text(text, encoding="utf-8")
        return True
    except OSError:
        return False


def _strategy_config(args: argparse.Namespace, strategy: str, calibration_row: Any | None = None) -> StrategyConfig:
    calibration_metrics = None
    calibration_status = None
    if calibration_row is not None:
        calibration_status = calibration_row["status"]
        if calibration_status == "calibrated_baseline":
            try:
                calibration_metrics = json.loads(calibration_row["metrics_json"])
            except (TypeError, json.JSONDecodeError):
                calibration_metrics = None
    return StrategyConfig(
        strategy_version=strategy,
        min_ev=args.min_ev,
        stake_units=args.stake_units,
        max_odds_age_seconds=args.max_odds_age_seconds,
        calibration_metrics=calibration_metrics,
        calibration_status=calibration_status,
    )


def _legacy_signal(provider: AiScoreProvider, ref: MatchRef, history) -> BetSignal | None:
    stats = provider.scraper.match_stats(ref.url, sport=ref.sport)
    return evaluate_match(stats, history)


def _pinnacle_setup(args: argparse.Namespace) -> tuple[Any | None, list]:
    """When the odds source is Pinnacle, build a client and pull the sport's
    live games once per scan. Returns (client, games); ((None, []) on any
    failure or when disabled) so the caller silently falls back."""
    if getattr(args, "odds_source", "pinnacle") != "pinnacle":
        return None, []
    try:
        client = pinnacle.PinnacleClient()
        games = client.live_games(args.sport)
        _print(args, f"Pinnacle: {len(games)} live {args.sport} game(s) for odds")
        return client, games
    except pinnacle.PinnacleError as exc:
        _print(args, f"Pinnacle unavailable, falling back to AiScore odds: {exc}")
        return None, []


def _pinnacle_odds_for(snapshot, ref: MatchRef, client, games, sport: str) -> tuple[OddsQuote, ...]:
    """Live sharp total for one match from Pinnacle, or () if not found/coherent.
    Joins on team names; AiScore stays the game-state source."""
    if client is None or not games:
        return ()
    game = pinnacle.find_game(ref.home_team, ref.away_team, games)
    if game is None:
        return ()
    try:
        total = client.main_total(game.matchup_id)
    except pinnacle.PinnacleError:
        return ()
    if total is None or not is_coherent_two_sided_odds(total.over_decimal, total.under_decimal):
        return ()
    return pinnacle.to_odds_quotes(total, snapshot, sport)


_ODDS_ESTIMATOR = OddsEstimator()


def _try_estimate_total_odds(
    store: QuicklinStore,
    match_id: int,
    snapshot,
    sport: str,
) -> tuple[tuple[OddsQuote, OddsQuote], dict[str, int], EstimatedOdds] | None:
    """Run OddsEstimator against a snapshot whose bookmaker odds are missing.
    If the estimator returns a payload at or above the confidence floor,
    persist the synthetic odds_quotes (so signals reference real ids) and
    return the wrapped quotes, their per-side row ids, and the estimator
    metadata. Returns None when estimation isn't possible or fails the
    confidence floor."""
    estimated = _ODDS_ESTIMATOR.estimate(snapshot, sport)
    if estimated is None:
        return None
    if estimated.confidence < ESTIMATOR_MIN_CONFIDENCE:
        return None
    try:
        over_quote, under_quote = estimated_odds_to_quotes(estimated, snapshot, sport)
    except Exception:
        return None
    side_to_id: dict[str, int] = {}
    for quote in (over_quote, under_quote):
        try:
            side_to_id[quote.side] = store.insert_odds_quote(match_id, quote)
        except Exception:
            # If we can't persist the synthetic quotes, we'd still rather
            # skip than emit a signal whose odds_quote_id is bogus.
            return None
    return (over_quote, under_quote), side_to_id, estimated


def _legacy_to_value_signal(
    signal: BetSignal,
    ref: MatchRef,
    odds_quotes,
    config: StrategyConfig,
) -> ValueSignal | None:
    side = "over" if "Over" in signal.pick else "under"
    quote = next((quote for quote in odds_quotes if quote.side == side), None)
    if quote is None:
        return None
    return ValueSignal(
        strategy_version=LEGACY_STRATEGY_VERSION,
        created_at=now_iso(),
        provider_match_id=ref.provider_match_id,
        canonical_id=ref.canonical_id,
        match_title=signal.match.title,
        url=signal.match.url,
        side=side,
        line=quote.line,
        offered_odds=quote.decimal_odds,
        fair_probability=0.0,
        fair_odds=None,
        expected_value=0.0,
        stake_units=config.stake_units,
        expected_goals_remaining=0.0,
        confidence=0.5,
        minute=signal.match.minute,
        score=f"{signal.match.home_score}-{signal.match.away_score}",
        bookmaker=quote.bookmaker,
        reason_codes=(LEGACY_STRATEGY_VERSION, *signal.reasons),
        sport=ref.sport,
    )


def _skip(
    store: QuicklinStore,
    args: argparse.Namespace,
    match_id: int | None,
    snapshot_id: int | None,
    reason: str,
    details: dict[str, Any] | None,
) -> None:
    strategy = args.strategy or default_strategy_for_sport(args.sport)
    store.insert_skip("aiscore", strategy, reason, details or {}, match_id, snapshot_id, args.sport)
    if args.include_skipped and not args.json:
        print(f"  skip: {reason}")


def _print(args: argparse.Namespace, message: str) -> None:
    if not args.json:
        print(message)


def _print_signal(args: argparse.Namespace, signal: ValueSignal, signal_id: int) -> None:
    if args.json:
        return
    print(
        f"  PLAY #{signal_id}: {signal.side.upper()} {signal.line} @ {signal.offered_odds} "
        f"| EV {signal.expected_value:.2%} | fair {signal.fair_odds or 0:.2f}"
    )
    print(
        "        "
        f"{signal.match_title} | {signal.minute}' {signal.score} | "
        f"total rem {signal.expected_total_remaining:.2f} | conf {signal.confidence:.2f}"
    )


def _csv_paths(args: argparse.Namespace) -> list[Path]:
    paths = list(args.csv)
    if args.csv_dir:
        paths.extend(sorted(args.csv_dir.glob("*.csv")))
    if not paths:
        raise SystemExit("Provide --csv path or --csv-dir for Football-Data import.")
    return paths


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        if not rows:
            file.write("")
            return
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
