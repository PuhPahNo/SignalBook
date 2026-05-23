from __future__ import annotations


STRATEGY_METADATA = {
    "soccer": {
        "default": "ev_totals_v1",
        "strategies": [
            {
                "id": "ev_totals_v1",
                "label": "Soccer EV totals",
                "status": "active baseline",
                "research": "Uses soccer-specific live stats and Football-Data historical priors, but remains paper-trading research.",
                "inputs": ["minute", "score", "shots on/off target", "attacks", "dangerous attacks", "corners", "red cards", "two-sided totals odds"],
                "gates": ["minute 1-88", "odds age <= 120s", "supported total line", "EV >= configured minimum"],
                "sources": ["Football-Data.co.uk historical soccer CSVs", "AIScore live soccer snapshots"],
            },
            {
                "id": "legacy_threshold_v1",
                "label": "Legacy threshold",
                "status": "comparison only",
                "research": "Preserves the old hand-coded threshold behavior for side-by-side comparison.",
                "inputs": ["minute", "score", "adjusted shots per 10", "AIScore H2H/team history"],
                "gates": ["history available", "legacy pace threshold", "matching total odds available"],
                "sources": ["AIScore live soccer snapshots", "AIScore H2H/team history"],
            },
        ],
    },
    "hockey": {
        "default": "hockey_totals_v1",
        "strategies": [
            {
                "id": "hockey_totals_v1",
                "label": "Hockey totals",
                "status": "calibration required",
                "research": "Uses market totals, live state, and the latest stored NHL league goal/shot baseline when calibration has run.",
                "inputs": ["period", "clock", "score", "shots on goal when exposed", "two-sided totals odds"],
                "gates": ["period or clock present", "supported total line", "two-sided odds", "EV >= configured minimum"],
                "sources": ["NHL team stats", "AIScore hockey live snapshots"],
            }
        ],
    },
    "basketball": {
        "default": "basketball_totals_v1",
        "strategies": [
            {
                "id": "basketball_totals_v1",
                "label": "Basketball totals",
                "status": "calibration required",
                "research": "Uses current score pace, market totals, and the latest stored NBA team points/pace baseline when calibration has run.",
                "inputs": ["quarter", "clock", "score", "pace proxy", "two-sided totals odds"],
                "gates": ["quarter or clock present", "supported total line", "two-sided odds", "EV >= configured minimum"],
                "sources": ["NBA team stats", "AIScore basketball live snapshots"],
            }
        ],
    },
    "baseball": {
        "default": "baseball_totals_v1",
        "strategies": [
            {
                "id": "baseball_totals_v1",
                "label": "Baseball totals",
                "status": "calibration required",
                "research": "Uses inning state, market totals, and the latest stored MLB run-rate baseline. It stays conservative because v1 still lacks base/out and pitcher context.",
                "inputs": ["inning state", "score", "two-sided totals odds"],
                "gates": ["inning state present", "supported total line", "two-sided odds", "EV >= configured minimum"],
                "sources": ["MLB Statcast/Baseball Savant", "AIScore baseball live snapshots"],
            }
        ],
    },
    "tennis": {
        "default": "tennis_match_totals_v1",
        "strategies": [
            {
                "id": "tennis_match_totals_v1",
                "label": "Tennis match totals",
                "status": "manual source required",
                "research": "Skips unless a supported match/game totals market exists; does not use clock assumptions.",
                "inputs": ["set state", "games/points when exposed", "two-sided match/game totals odds"],
                "gates": ["set state present", "supported totals market", "two-sided odds", "EV >= configured minimum"],
                "sources": ["ATP Stats", "AIScore tennis live snapshots"],
            }
        ],
    },
}


def strategy_metadata() -> dict:
    return STRATEGY_METADATA
