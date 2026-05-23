# PRD 03: EV Strategy Engine

## Goal
Replace threshold-only picks with exact-line expected-value decisions while keeping the legacy model available for comparison.

## Requirements
- Keep `legacy_threshold_v1` behavior.
- Add default strategy `ev_totals_v1`.
- Use current minute, score, shots, attacks, dangerous attacks, corners, red cards, historical priors, and market total line.
- Convert two-sided odds into no-vig market probabilities for feature logging.
- Emit signals only when stats exist, odds exist, the line is supported, and EV is at least 3%.
- Support `.0`, `.25`, `.5`, and `.75` totals with proper half-stake settlement math.

## Acceptance Criteria
- No signal is emitted without a line, side, odds, fair probability, EV, and settlement path.
- Quarter-line EV and settlement are unit-tested.
- Legacy and EV strategy versions are both identifiable in storage.
