# PRD 02: Historical Backtesting

## Goal
Use free Football-Data.co.uk CSVs to build an honest historical baseline, while clearly separating it from live in-play validation.

## Requirements
- Import Football-Data CSV rows into SQLite.
- Support common columns: teams, scores, half-time scores, shots, shots on target, corners, cards, and over/under 2.5 odds.
- Backtest a baseline pre-match totals model.
- Report ROI, yield, hit rate, Brier score, log loss, calibration buckets, and a closing-line-value proxy when closing columns exist.

## Acceptance Criteria
- A fixture CSV can be imported without pandas.
- Backtest output is JSON-serializable.
- Reports state that historical results are baseline-only because free data does not provide live in-play snapshots.
