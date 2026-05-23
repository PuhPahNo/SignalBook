# PRD 06: Hockey Integration

## Goal
Add hockey as the first non-soccer sport while introducing the reusable multi-sport foundation.

## Requirements
- Add `sport` support across match refs, snapshots, odds quotes, predictions, signals, skips, provider health, runs, and reports.
- AIScore hockey remains the primary source.
- Parse live match refs, score, period, clock, shots on goal when available, and full-match totals odds when available.
- Add strategy `hockey_totals_v1`.
- Emit hockey totals signals only when period/clock, line, side, two-sided odds, probability, EV, and settlement path are valid.
- Store missing period/clock, missing odds, stale odds, unsupported line, and no-edge skip reasons.
- Add hockey filters and columns to the local website.

## Acceptance Criteria
- `signalbook.py scan --sport hockey --limit 3` runs without breaking soccer mode.
- Hockey scans store matches/skips even when AIScore lacks usable totals odds.
- Website summaries and tables can filter to hockey.
- No automatic bet placement exists.
