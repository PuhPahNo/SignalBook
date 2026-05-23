# PRD 07: Basketball Integration

## Goal
Add basketball live totals research using the same multi-sport foundation created for hockey.

## Requirements
- AIScore basketball remains the primary source.
- Parse live match refs, score, quarter, clock, and available team stats such as shooting, rebounds, turnovers, steals, fouls, or raw stat payloads.
- Add strategy `basketball_totals_v1`.
- Model remaining points using current score, elapsed game time, market total, and available pace/stat context.
- Emit signals only when quarter/clock, supported totals line, two-sided odds, EV, and settlement path are valid.
- Add basketball dashboard filters, summaries, snapshots, skips, and provider-health views.

## Acceptance Criteria
- `signalbook.py scan --sport basketball --limit 3` runs without breaking soccer or hockey mode.
- Basketball missing-data cases are stored as skipped candidates.
- Website sport tabs update basketball summaries and table rows.
