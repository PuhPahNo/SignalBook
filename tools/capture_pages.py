"""One-off diagnostic: load a few finished baseball/tennis match pages and
dump the raw detail-page text + what the current parser extracts. Read-only
(no DB writes). Output goes to data/debug/. Delete this file when done."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quicklin_soccer.aiscore import BrowserConfig
from quicklin_soccer.models import MatchRef
from quicklin_soccer.providers import AiScoreProvider

DEBUG_DIR = ROOT / "data" / "debug"
PER_SPORT = 3


def refs(conn: sqlite3.Connection, sport: str, limit: int) -> list[MatchRef]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT provider, provider_match_id, url, home_team, away_team, league, start_time, sport "
        "FROM matches WHERE sport=? LIMIT ?",
        (sport, limit),
    ).fetchall()
    return [MatchRef(**dict(r)) for r in rows]


def main() -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROOT / "data" / "quicklin.db")
    targets = refs(conn, "baseball", PER_SPORT) + refs(conn, "tennis", PER_SPORT)
    conn.close()
    print(f"Capturing {len(targets)} pages (headed Chrome)...\n")

    config = BrowserConfig(headless=False, wait_timeout=25, page_load_timeout=35)
    with AiScoreProvider.create(config) as provider:
        for ref in targets:
            print(f"=== {ref.sport}: {ref.url}")
            try:
                result = provider.settle_match(ref)
            except Exception as exc:  # noqa: BLE001 - diagnostic, keep going
                print(f"    ERROR: {type(exc).__name__}: {exc}\n")
                continue
            text = result.get("page_text") or ""
            print(
                f"    score_source={result.get('score_source')!r} "
                f"is_final={result.get('is_final')!r} status_id={result.get('status_id')!r} "
                f"phase={result.get('phase')!r} minute={result.get('minute')!r} "
                f"home={result.get('home_score')!r} away={result.get('away_score')!r} "
                f"text_len={len(text)}"
            )
            slug = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{ref.sport}_{ref.provider_match_id}")[:120]
            (DEBUG_DIR / f"{slug}.txt").write_text(text, encoding="utf-8")
            print(f"    wrote {slug}.txt\n")


if __name__ == "__main__":
    main()
