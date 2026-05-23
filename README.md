# SignalBook Multi-Sport

SignalBook is a local multi-sport betting-research tool. It scans AIScore live matches, stores every snapshot in SQLite, evaluates exact live totals lines for expected value, paper-trades signals, settles them later, and produces local reports.

It does not place real bets.

## Setup

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.txt
```

AIScore-only mode works without API keys. Optional free-tier keys can be set for future cross-check adapters:

```bash
export API_FOOTBALL_KEY="..."
export THE_ODDS_API_KEY="..."
```

## Live Research

Run one scan and store results in the local SQLite database:

```bash
.venv/bin/python signalbook.py scan --sport soccer --limit 10 --include-skipped
```

Supported sports are `soccer`, `hockey`, `basketball`, `baseball`, and `tennis`. Soccer remains the most mature strategy; the other sports use conservative AIScore-first totals models and skip candidates when AIScore does not expose enough state or odds.

Monitor repeatedly for forward paper trading:

```bash
.venv/bin/python signalbook.py monitor --sport hockey --limit 25 --interval-seconds 60
```

Settle open paper-trading signals:

```bash
.venv/bin/python signalbook.py settle
```

Generate reports:

```bash
.venv/bin/python signalbook.py report --output-dir output/report
```

Refresh free-source calibration baselines:

```bash
.venv/bin/python signalbook.py calibrate --sport all
```

Start the local website interface:

```bash
.venv/bin/python signalbook.py web
```

Then open `http://127.0.0.1:8765`. The dark web console uses the same SQLite database and scanner commands as the CLI. It includes sport tabs and shows whether the provider found no live match refs, found matches that were skipped, or found matches that cleared the EV threshold.

## Historical Baseline

Import free Football-Data.co.uk CSVs and run the baseline backtest:

```bash
.venv/bin/python signalbook.py backtest --csv path/to/E0.csv --season 2025-2026
```

This is a historical baseline only. Free public data does not provide historical live in-play odds snapshots, so live strategy validation comes from forward paper trading.

## Strategies

- `ev_totals_v1` is the default. It estimates remaining goals, prices the exact live line, and emits only positive-EV signals.
- `legacy_threshold_v1` preserves the old threshold logic for comparison.
- `hockey_totals_v1`, `basketball_totals_v1`, and `baseball_totals_v1` use the latest stored free-source league baselines when calibration has run.
- `tennis_match_totals_v1` stays conservative because a stable free ATP JSON calibration source is not wired in yet.

Outputs are candidate signals for research, not guaranteed profitable bets.
