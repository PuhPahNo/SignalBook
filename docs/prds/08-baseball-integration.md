# PRD 08: Baseball Integration

## Goal
Add conservative baseball totals research without overstating confidence in a state-heavy sport.

## Requirements
- AIScore baseball remains the primary source.
- Parse live match refs, score, inning/half-inning, outs/base state if exposed, pitcher/team context if exposed, and totals odds when available.
- Add strategy `baseball_totals_v1`.
- Require inning state and two-sided totals odds before pricing a candidate.
- Use conservative confidence defaults and skip when critical baseball state is missing.
- Add baseball settlement and website support.

## Acceptance Criteria
- `signalbook.py scan --sport baseball --limit 3` runs without breaking other sports.
- Signals cannot be emitted without inning state, valid odds, valid line, probability, EV, and settlement path.
- Website summaries and skip reasons make missing baseball state explicit.
