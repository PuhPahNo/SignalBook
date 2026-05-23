# PRD 01: Data Foundation

## Goal
Create a durable local research database so every match, snapshot, odds quote, prediction, signal, skip, provider check, run, and settlement can be audited later.

## Requirements
- Use SQLite at `data/quicklin.db` by default.
- Store canonical match IDs plus provider-specific IDs.
- Preserve raw provider payloads where practical.
- Providers expose `list_live_matches`, `get_live_snapshot`, `get_odds`, `get_historical_matches`, and `settle_match` semantics.
- Missing optional API keys must not break AIScore-only mode.

## Acceptance Criteria
- A scan can insert matches, snapshots, odds, predictions, signals, and skips.
- Open signals can later be queried for settlement.
- Database creation requires no external service.
