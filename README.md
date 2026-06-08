# SignalBook Multi-Sport

SignalBook is a local multi-sport betting-research tool. It scans AIScore live matches, stores every snapshot in SQLite, and — at a fixed per-sport checkpoint — locks ONE live over/under prediction per game from in-game performance, graded against a reference total (Pinnacle's sharp opening line where available, else AIScore's). Predictions settle at game end and are scored by **win rate vs base rate** (edge = the model's skill over always picking one side).

It does not place real bets. The default `--mode predict` is a prediction/accuracy tracker, not a value-betting bot: live in-play odds aren't available for free (both AIScore and Pinnacle's guest API serve only frozen pre-match totals), so EV/ROI can't be computed honestly. The legacy EV value-betting path remains under `--mode value`.

## Requirements

- Python 3.11 or newer.
- Google Chrome or Chromium installed locally.
- Internet access for AIScore scraping and optional calibration sources.
- No paid API services are required.

SignalBook uses Selenium 4, which can manage the matching ChromeDriver automatically on most local machines. If Selenium cannot start Chrome, update Chrome first, then rerun the command.

## Fresh Clone Quickstart

```bash
git clone https://github.com/PuhPahNo/SignalBook.git
cd SignalBook
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python signalbook.py web
```

Then open `http://127.0.0.1:8765`.

The web command starts the local dashboard and automatically starts the SignalBook bot. The bot scans supported sports on a loop, stores snapshots/signals in a local SQLite database, and attempts settlement checks for open paper-trading signals.

On Windows, replace `.venv/bin/python` with `.venv\Scripts\python.exe`.

## Optional API Keys

```bash
export API_FOOTBALL_KEY="..."
export THE_ODDS_API_KEY="..."
```

AIScore-only mode works without API keys. Optional free-tier keys can be set for future cross-check adapters; missing keys do not break the app.

## Local Files

- `data/quicklin.db` is created automatically on first run and stores local paper-trading history.
- `output/report/` is created when reports are generated.
- Both are intentionally ignored by git so personal local results are not pushed publicly.

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

The dashboard runs locally. Do not expose it directly to the public internet without adding authentication and moving storage to a production database.

## Historical Baseline

Import free Football-Data.co.uk CSVs and run the baseline backtest:

```bash
.venv/bin/python signalbook.py backtest --csv path/to/E0.csv --season 2025-2026
```

This is a historical baseline only. Free public data does not provide historical live in-play odds snapshots, so live strategy validation comes from forward paper trading.

## Prediction model (`--mode predict`, default)

At each sport's checkpoint (soccer 45', basketball 24', hockey 30', baseball 4th inning, tennis 2nd set) the model locks one over/under call per game. Expected remaining scoring is estimated from in-game pace (not anchored to the line), the side with the higher modelled probability is taken, and it's settled at game end. The Performance view reports hit rate, base rate, and edge per sport, strategy, and line source.

## Strategies (used for the prediction's probability, and for `--mode value`)

- `ev_totals_v1` is the soccer default. It estimates remaining goals and prices the live line.
- `legacy_threshold_v1` preserves the old threshold logic for comparison.
- `hockey_totals_v1`, `basketball_totals_v1`, and `baseball_totals_v1` use the latest stored free-source league baselines when calibration has run.
- `tennis_match_totals_v1` stays conservative because a stable free ATP JSON calibration source is not wired in yet.

Outputs are candidate signals for research, not guaranteed profitable bets.

## Troubleshooting

- If Chrome does not launch, update Chrome or install Chromium, then rerun the command.
- If no predictions appear, check the Skips view: games may be before their checkpoint, already predicted, or missing a reference line (no Pinnacle match and no AIScore total).
- If AIScore changes its page markup, parser tests may still pass while live scraping needs an update.
- Delete `data/quicklin.db` only if you intentionally want to reset local paper-trading history.
