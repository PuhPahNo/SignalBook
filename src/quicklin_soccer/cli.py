from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

from quicklin_soccer.aiscore import BrowserConfig, StatsUnavailable, classify_error
from quicklin_soccer.calibration import run_calibrations
from quicklin_soccer.football_data import backtest_historical_baseline, import_football_data_csv
from quicklin_soccer.models import (
    BetSignal,
    MatchRef,
    ProviderHealth,
    Settlement,
    ValueSignal,
    now_iso,
)
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
                for index, ref in enumerate(refs, start=1):
                    _print(args, f"[{index}/{len(refs)}] {ref.home_team} vs {ref.away_team}")
                    match_id = store.upsert_match(ref)
                    try:
                        snapshot, odds_quotes = provider.get_live_snapshot(ref)
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
                            if converted is None:
                                _skip(store, args, match_id, snapshot_id, "legacy_missing_odds", {})
                                summary["skips"] += 1
                                continue
                            signal_id, is_new = _insert_signal_if_new(
                                store,
                                match_id,
                                snapshot_id,
                                None,
                                odds_ids.get(converted.side),
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
                        prediction_id = None
                        if evaluation.prediction:
                            prediction_id = store.insert_prediction(match_id, snapshot_id, evaluation.prediction)

                        if not evaluation.signals:
                            _skip(store, args, match_id, snapshot_id, evaluation.skip_reason or "no_signal", evaluation.details)
                            summary["skips"] += 1
                            continue

                        for signal in evaluation.signals:
                            signal_id, is_new = _insert_signal_if_new(
                                store,
                                match_id,
                                snapshot_id,
                                prediction_id,
                                odds_ids.get(signal.side),
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
    existing_id = store.emitted_signal_id(match_id, signal)
    if existing_id is not None:
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
    print(
        "SignalBook bot running: "
        f"scanning {', '.join(sports)} every {args.scan_interval_seconds}s; "
        f"settling every {args.settle_interval_seconds}s."
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

        now = time.monotonic()
        if now - last_settle_at >= args.settle_interval_seconds:
            try:
                settle_command(_bot_settle_args(args))
            except Exception as exc:
                print(f"settlement pass failed but bot is continuing: {classify_error(exc)}")
            last_settle_at = time.monotonic()

        if args.loops is not None and loops >= args.loops:
            break
        elapsed = time.monotonic() - loop_started
        time.sleep(max(0, args.scan_interval_seconds - elapsed))
    return 0


def settle_command(args: argparse.Namespace) -> int:
    settled = 0
    skipped = 0
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
                result = provider.settle_match(ref)
                if not args.force and not _is_settlement_ready(ref.sport, result):
                    skipped += 1
                    _print(args, f"skip open signal {row['id']}: match not final")
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

    if args.json:
        print(json.dumps({"settled": settled, "skipped": skipped}, indent=2))
    else:
        print(f"Done. Settled {settled}, skipped {skipped}.")
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
    bot.add_argument("--headful", action="store_true", help="Show Chrome while scraping.")
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
    settle.add_argument("--headful", action="store_true")
    settle.add_argument("--wait-timeout", type=int, default=25)
    settle.add_argument("--page-timeout", type=int, default=35)
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
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--sport", choices=SUPPORTED_SPORTS, default=SPORT_SOCCER)
    parser.add_argument("--limit", type=int, help="Maximum live matches to scan.")
    parser.add_argument("--headful", action="store_true", help="Show Chrome while scraping.")
    parser.add_argument("--include-skipped", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--wait-timeout", type=int, default=25)
    parser.add_argument("--page-timeout", type=int, default=35)
    parser.add_argument("--strategy", choices=SPORT_STRATEGY_VERSIONS, default=None)
    parser.add_argument("--min-ev", type=float, default=0.03)
    parser.add_argument("--stake-units", type=float, default=1.0)
    parser.add_argument("--max-odds-age-seconds", type=int, default=120)


def _browser_config(args: argparse.Namespace) -> BrowserConfig:
    return BrowserConfig(
        headless=not args.headful,
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
        headful=args.headful,
        include_skipped=args.include_skipped,
        json=False,
        wait_timeout=args.wait_timeout,
        page_timeout=args.page_timeout,
        strategy=None,
        min_ev=args.min_ev,
        stake_units=args.stake_units,
        max_odds_age_seconds=args.max_odds_age_seconds,
        output=None,
    )


def _bot_settle_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        db=args.db,
        force=False,
        json=False,
        headful=args.headful,
        wait_timeout=args.wait_timeout,
        page_timeout=args.page_timeout,
    )


def _is_settlement_ready(sport: str, result: dict[str, Any]) -> bool:
    if sport != SPORT_SOCCER and result.get("score_source") != "detail_scoreboard":
        return False
    if result.get("is_final"):
        return True
    if result.get("status_id") in {8, 10, 11, 12, 13}:
        return True
    if sport == SPORT_SOCCER:
        return int(result.get("minute") or 0) >= 90
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
