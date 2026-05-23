# PRD 05: Reporting Release

## Goal
Produce useful local reports for research and paper-trading review.

## Requirements
- Generate HTML and CSV reports from SQLite.
- Include daily/weekly-ready aggregates, strategy comparison, league breakdown, open signals, settled signals, provider health, and unresolved settlements.
- README documents no-key mode and optional free API-key mode.
- Reports and docs clearly label outputs as candidate signals, not guaranteed profitable bets.

## Acceptance Criteria
- `signalbook.py report` writes an HTML dashboard plus CSV exports.
- Reports separate historical baseline results from live forward paper-trading records.
