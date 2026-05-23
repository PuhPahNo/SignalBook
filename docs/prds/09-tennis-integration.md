# PRD 09: Tennis Integration

## Goal
Add tennis match totals research while avoiding clock-based assumptions.

## Requirements
- AIScore tennis remains the primary source.
- Parse live match refs, set state, games/points/tiebreak state when available, and match/game totals odds when available.
- Add strategy `tennis_match_totals_v1`.
- Prefer match/game totals markets; if AIScore exposes only moneyline or unsupported odds, record an explicit skip.
- Do not use minute/clock language in tennis UI columns beyond generic fallback fields.
- Add tennis dashboard filters, skips, snapshots, and reporting support.

## Acceptance Criteria
- `signalbook.py scan --sport tennis --limit 3` runs without breaking other sports.
- Tennis candidates with no supported totals market are skipped with an auditable reason.
- Website sport tabs can show tennis-only state without claiming clock-based modeling.
