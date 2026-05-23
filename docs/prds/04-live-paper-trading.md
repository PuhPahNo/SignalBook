# PRD 04: Live Paper Trading

## Goal
Run the system forward like a paper-trading desk: capture live snapshots, issue candidate signals, settle them later, and never place real bets.

## Requirements
- Commands: `scan`, `monitor`, `settle`, `backtest`, `report`.
- `monitor` repeats scans every 60 seconds by default.
- One flat paper unit per signal by default.
- Store every skipped candidate with a reason.
- Settlement supports win, half win, push, half loss, split, and loss.

## Acceptance Criteria
- One provider failure does not stop the whole scan.
- Open signals are queryable and settleable.
- No automatic bet placement exists.
