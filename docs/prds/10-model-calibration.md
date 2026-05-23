# PRD 10: Model Calibration

## Goal
Replace unvalidated multi-sport scaffolds with calibrated, source-backed strategy versions before treating non-soccer outputs as candidate betting signals.

## Requirements
- Build one calibration pipeline per sport using public or free official/statistical sources.
- Store calibration runs, source URLs, date ranges, sample sizes, fitted parameters, and validation metrics in SQLite or reproducible local JSON artifacts.
- Soccer keeps Football-Data.co.uk as the historical baseline and forward paper trading as live validation.
- Hockey calibration should use NHL team stats plus captured AIScore live hockey snapshots.
- Basketball calibration should use NBA team stats plus captured AIScore live basketball snapshots.
- Baseball calibration should use MLB Statcast/Baseball Savant concepts plus captured AIScore live baseball snapshots.
- Tennis calibration should use ATP stats plus captured AIScore live tennis snapshots.
- A strategy cannot be marked calibrated until it has documented sample size, calibration error, ROI/yield paper-trade results, and a reproducible report.

## Acceptance Criteria
- The website shows whether each strategy is calibrated, unvalidated, or comparison-only.
- Non-soccer strategy docs name the data source and what features are actually calibrated.
- Reports separate unvalidated scaffold results from calibrated strategy results.
- Calibration updates are versioned instead of silently changing existing strategy behavior.
