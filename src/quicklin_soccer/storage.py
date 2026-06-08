from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from quicklin_soccer.models import (
    LiveSnapshot,
    MatchRef,
    OddsQuote,
    ProviderHealth,
    Settlement,
    StrategyPrediction,
    ValueSignal,
    now_iso,
)


DEFAULT_DB_PATH = Path("data/quicklin.db")


class QuicklinStore:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # timeout=15 lets a writer wait up to 15s for the lock instead of
        # immediately failing — needed now that the bot loop and the settle
        # subprocess share this database.
        self.conn = sqlite3.connect(self.path, timeout=15)
        self.conn.row_factory = sqlite3.Row
        # WAL mode allows the scan loop to keep reading while the settle
        # subprocess is writing, and vice versa. Safe to set every time;
        # SQLite no-ops it if already in WAL.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        self.init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "QuicklinStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_id TEXT NOT NULL UNIQUE,
                sport TEXT NOT NULL DEFAULT 'soccer',
                provider TEXT NOT NULL,
                provider_match_id TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                league TEXT,
                start_time TEXT,
                url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(provider, provider_match_id)
            );

            CREATE TABLE IF NOT EXISTS live_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL REFERENCES matches(id),
                sport TEXT NOT NULL DEFAULT 'soccer',
                provider TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                phase TEXT,
                clock TEXT,
                minute INTEGER NOT NULL,
                status TEXT NOT NULL,
                home_score INTEGER NOT NULL,
                away_score INTEGER NOT NULL,
                home_attacks INTEGER NOT NULL,
                away_attacks INTEGER NOT NULL,
                home_dangerous_attacks INTEGER NOT NULL,
                away_dangerous_attacks INTEGER NOT NULL,
                home_shots_on_target INTEGER NOT NULL,
                away_shots_on_target INTEGER NOT NULL,
                home_shots_off_target INTEGER NOT NULL,
                away_shots_off_target INTEGER NOT NULL,
                home_corners INTEGER NOT NULL,
                away_corners INTEGER NOT NULL,
                home_red_cards INTEGER NOT NULL DEFAULT 0,
                away_red_cards INTEGER NOT NULL DEFAULT 0,
                stats_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS odds_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL REFERENCES matches(id),
                sport TEXT NOT NULL DEFAULT 'soccer',
                provider TEXT NOT NULL,
                bookmaker TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                market TEXT NOT NULL,
                period TEXT NOT NULL,
                line REAL NOT NULL,
                side TEXT NOT NULL,
                decimal_odds REAL NOT NULL,
                is_suspended INTEGER NOT NULL DEFAULT 0,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS strategy_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL REFERENCES matches(id),
                snapshot_id INTEGER REFERENCES live_snapshots(id),
                sport TEXT NOT NULL DEFAULT 'soccer',
                strategy_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                fair_probability REAL NOT NULL,
                fair_odds REAL,
                expected_goals_remaining REAL NOT NULL,
                expected_total_remaining REAL NOT NULL DEFAULT 0,
                confidence REAL NOT NULL,
                feature_json TEXT NOT NULL,
                reason_codes TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS value_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER NOT NULL REFERENCES matches(id),
                snapshot_id INTEGER REFERENCES live_snapshots(id),
                prediction_id INTEGER REFERENCES strategy_predictions(id),
                odds_quote_id INTEGER REFERENCES odds_quotes(id),
                sport TEXT NOT NULL DEFAULT 'soccer',
                strategy_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                side TEXT NOT NULL,
                line REAL NOT NULL,
                offered_odds REAL NOT NULL,
                fair_probability REAL NOT NULL,
                fair_odds REAL,
                ev REAL NOT NULL,
                stake_units REAL NOT NULL,
                expected_goals_remaining REAL NOT NULL,
                expected_total_remaining REAL NOT NULL DEFAULT 0,
                confidence REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                reason_codes TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL UNIQUE REFERENCES value_signals(id),
                match_id INTEGER NOT NULL REFERENCES matches(id),
                settled_at TEXT NOT NULL,
                final_home_score INTEGER NOT NULL,
                final_away_score INTEGER NOT NULL,
                result TEXT NOT NULL,
                payout_units REAL NOT NULL,
                raw_json TEXT
            );

            CREATE TABLE IF NOT EXISTS provider_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL DEFAULT 'soccer',
                provider TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                ok INTEGER NOT NULL,
                message TEXT NOT NULL,
                latency_ms INTEGER
            );

            CREATE TABLE IF NOT EXISTS run_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL DEFAULT 'soccer',
                run_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                summary_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS skipped_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id INTEGER REFERENCES matches(id),
                snapshot_id INTEGER REFERENCES live_snapshots(id),
                sport TEXT NOT NULL DEFAULT 'soccer',
                provider TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                reason TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS historical_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL DEFAULT 'soccer',
                source TEXT NOT NULL,
                division TEXT,
                season TEXT,
                match_date TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                fthg INTEGER,
                ftag INTEGER,
                hthg INTEGER,
                htag INTEGER,
                home_shots INTEGER,
                away_shots INTEGER,
                home_shots_target INTEGER,
                away_shots_target INTEGER,
                home_corners INTEGER,
                away_corners INTEGER,
                home_red_cards INTEGER,
                away_red_cards INTEGER,
                over25_odds REAL,
                under25_odds REAL,
                close_over25_odds REAL,
                close_under25_odds REAL,
                raw_json TEXT NOT NULL,
                UNIQUE(source, division, season, match_date, home_team, away_team)
            );

            CREATE TABLE IF NOT EXISTS calibration_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sport TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                season TEXT,
                status TEXT NOT NULL,
                sample_size INTEGER NOT NULL DEFAULT 0,
                metrics_json TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            -- Records every scan in which the model would have emitted a
            -- new value_signal but was blocked by the open-trade dedup.
            -- Used for post-hoc analysis: did the dedup-by-side policy
            -- cost us EV when the line moved? Each row is a "would-have-bet"
            -- snapshot linked back to the original open signal.
            CREATE TABLE IF NOT EXISTS line_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL REFERENCES value_signals(id),
                match_id INTEGER NOT NULL,
                sport TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                side TEXT NOT NULL,
                observed_line REAL NOT NULL,
                observed_odds REAL,
                observed_at TEXT NOT NULL,
                odds_source TEXT NOT NULL DEFAULT 'market',
                odds_confidence REAL
            );

            CREATE INDEX IF NOT EXISTS idx_line_movements_signal
            ON line_movements(signal_id);
            CREATE INDEX IF NOT EXISTS idx_line_movements_match
            ON line_movements(match_id, observed_at);

            CREATE INDEX IF NOT EXISTS idx_value_signals_open_unique
            ON value_signals(match_id, sport, strategy_version, side, line, status);
            """
        )
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self) -> None:
        self._ensure_column("matches", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("live_snapshots", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("live_snapshots", "phase", "TEXT")
        self._ensure_column("live_snapshots", "clock", "TEXT")
        self._ensure_column("live_snapshots", "stats_json", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("odds_quotes", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("strategy_predictions", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("strategy_predictions", "expected_total_remaining", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("value_signals", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("value_signals", "expected_total_remaining", "REAL NOT NULL DEFAULT 0")
        self._ensure_column("provider_health", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("run_metadata", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("skipped_candidates", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        self._ensure_column("historical_matches", "sport", "TEXT NOT NULL DEFAULT 'soccer'")
        # Odds-estimator columns (Anthony's "estimated paper trades" feature).
        # These let the dashboard render estimated signals visually distinct,
        # while the settle flow treats them identically to market-priced ones.
        self._ensure_column("odds_quotes", "is_estimated", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("odds_quotes", "estimation_method", "TEXT")
        self._ensure_column("odds_quotes", "estimation_confidence", "REAL")
        self._ensure_column("value_signals", "odds_source", "TEXT NOT NULL DEFAULT 'market'")
        self._ensure_column("value_signals", "odds_confidence", "REAL")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_match(self, ref: MatchRef) -> int:
        now = now_iso()
        existing = self.conn.execute(
            "SELECT id FROM matches WHERE provider = ? AND provider_match_id = ?",
            (ref.provider, ref.provider_match_id),
        ).fetchone()
        if existing:
            self.conn.execute(
                """
                UPDATE matches
                SET canonical_id = ?, sport = ?, home_team = ?, away_team = ?,
                    league = ?, start_time = ?, url = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    ref.canonical_id,
                    ref.sport,
                    ref.home_team,
                    ref.away_team,
                    ref.league,
                    ref.start_time,
                    ref.url,
                    now,
                    existing["id"],
                ),
            )
            self.conn.commit()
            return int(existing["id"])
        self.conn.execute(
            """
            INSERT INTO matches (
                canonical_id, sport, provider, provider_match_id, home_team, away_team,
                league, start_time, url, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_id) DO UPDATE SET
                sport=excluded.sport,
                provider=excluded.provider,
                provider_match_id=excluded.provider_match_id,
                home_team=excluded.home_team,
                away_team=excluded.away_team,
                league=excluded.league,
                start_time=excluded.start_time,
                url=excluded.url,
                updated_at=excluded.updated_at
            """,
            (
                ref.canonical_id,
                ref.sport,
                ref.provider,
                ref.provider_match_id,
                ref.home_team,
                ref.away_team,
                ref.league,
                ref.start_time,
                ref.url,
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.match_id_for_canonical(ref.canonical_id)

    def match_id_for_canonical(self, canonical_id: str) -> int:
        row = self.conn.execute("SELECT id FROM matches WHERE canonical_id = ?", (canonical_id,)).fetchone()
        if row is None:
            raise KeyError(f"match not found: {canonical_id}")
        return int(row["id"])

    def insert_snapshot(self, match_id: int, snapshot: LiveSnapshot) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO live_snapshots (
                match_id, sport, provider, captured_at, phase, clock, minute, status, home_score, away_score,
                home_attacks, away_attacks, home_dangerous_attacks, away_dangerous_attacks,
                home_shots_on_target, away_shots_on_target, home_shots_off_target,
                away_shots_off_target, home_corners, away_corners, home_red_cards,
                away_red_cards, stats_json, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                snapshot.sport,
                snapshot.provider,
                snapshot.captured_at,
                snapshot.phase,
                snapshot.clock,
                snapshot.minute,
                snapshot.status,
                snapshot.home_score,
                snapshot.away_score,
                snapshot.home_attacks,
                snapshot.away_attacks,
                snapshot.home_dangerous_attacks,
                snapshot.away_dangerous_attacks,
                snapshot.home_shots_on_target,
                snapshot.away_shots_on_target,
                snapshot.home_shots_off_target,
                snapshot.away_shots_off_target,
                snapshot.home_corners,
                snapshot.away_corners,
                snapshot.home_red_cards,
                snapshot.away_red_cards,
                _json(snapshot.stats),
                _json(snapshot.raw),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def insert_odds_quote(self, match_id: int, quote: OddsQuote) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO odds_quotes (
                match_id, sport, provider, bookmaker, captured_at, market, period, line, side,
                decimal_odds, is_suspended, is_blocked, raw_json,
                is_estimated, estimation_method, estimation_confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                quote.sport,
                quote.provider,
                quote.bookmaker,
                quote.captured_at,
                quote.market,
                quote.period,
                quote.line,
                quote.side,
                quote.decimal_odds,
                int(quote.is_suspended),
                int(quote.is_blocked),
                _json(quote.raw),
                int(quote.is_estimated),
                quote.estimation_method,
                quote.estimation_confidence,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def insert_prediction(self, match_id: int, snapshot_id: int | None, prediction: StrategyPrediction) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO strategy_predictions (
                match_id, snapshot_id, sport, strategy_version, created_at, fair_probability, fair_odds,
                expected_goals_remaining, expected_total_remaining, confidence, feature_json, reason_codes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                snapshot_id,
                prediction.sport,
                prediction.strategy_version,
                prediction.created_at,
                prediction.fair_probability,
                prediction.fair_odds,
                prediction.expected_goals_remaining,
                prediction.expected_total_remaining,
                prediction.confidence,
                _json(prediction.feature_values),
                _json(prediction.reason_codes),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def insert_signal(
        self,
        match_id: int,
        snapshot_id: int | None,
        prediction_id: int | None,
        odds_quote_id: int | None,
        signal: ValueSignal,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO value_signals (
                match_id, snapshot_id, prediction_id, odds_quote_id, sport, strategy_version, created_at,
                side, line, offered_odds, fair_probability, fair_odds, ev, stake_units,
                expected_goals_remaining, expected_total_remaining, confidence, reason_codes,
                odds_source, odds_confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_id,
                snapshot_id,
                prediction_id,
                odds_quote_id,
                signal.sport,
                signal.strategy_version,
                signal.created_at,
                signal.side,
                signal.line,
                signal.offered_odds,
                signal.fair_probability,
                signal.fair_odds,
                signal.expected_value,
                signal.stake_units,
                signal.expected_goals_remaining,
                signal.expected_total_remaining,
                signal.confidence,
                _json(signal.reason_codes),
                signal.odds_source,
                signal.odds_confidence,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def open_signal_id(self, match_id: int, signal: ValueSignal) -> int | None:
        row = self.conn.execute(
            """
            SELECT id
            FROM value_signals
            WHERE match_id = ?
              AND sport = ?
              AND strategy_version = ?
              AND side = ?
              AND line = ?
              AND status = 'open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (match_id, signal.sport, signal.strategy_version, signal.side, signal.line),
        ).fetchone()
        return int(row["id"]) if row else None

    def insert_line_movement(
        self,
        signal_id: int,
        match_id: int,
        signal: ValueSignal,
    ) -> int:
        """Record the would-have-emitted line/odds for a scan that was
        blocked by the open-trade dedup. Lets Anthony measure later
        whether holding the original bet through line movement cost
        EV vs re-pricing on each scan."""
        cursor = self.conn.execute(
            """
            INSERT INTO line_movements (
                signal_id, match_id, sport, strategy_version, side,
                observed_line, observed_odds, observed_at,
                odds_source, odds_confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                match_id,
                signal.sport,
                signal.strategy_version,
                signal.side,
                signal.line,
                signal.offered_odds,
                now_iso(),
                signal.odds_source,
                signal.odds_confidence,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def has_signal_for_match(self, match_id: int) -> bool:
        """True if any signal already exists for this match. The win-rate model
        locks exactly ONE prediction per game (at the checkpoint), so this guards
        against re-predicting on every later scan of the same match."""
        row = self.conn.execute(
            "SELECT 1 FROM value_signals WHERE match_id = ? LIMIT 1", (match_id,)
        ).fetchone()
        return row is not None

    def emitted_signal_id(self, match_id: int, signal: ValueSignal) -> int | None:
        """Return the id of an existing OPEN signal for this match+side that
        we should dedupe against, regardless of line. The bot calls this
        before emitting a new value_signal — if a paper trade is already
        open on this side of this market, the new candidate is a duplicate
        (the original bet stands until settled). A moved line does NOT
        spawn a new paper trade; the move is logged separately to
        line_movements for post-hoc EV analysis."""
        row = self.conn.execute(
            """
            SELECT id
            FROM value_signals
            WHERE match_id = ?
              AND sport = ?
              AND strategy_version = ?
              AND side = ?
              AND status = 'open'
            ORDER BY id DESC
            LIMIT 1
            """,
            (match_id, signal.sport, signal.strategy_version, signal.side),
        ).fetchone()
        return int(row["id"]) if row else None

    def insert_skip(
        self,
        provider: str,
        strategy_version: str,
        reason: str,
        details: dict[str, Any] | None = None,
        match_id: int | None = None,
        snapshot_id: int | None = None,
        sport: str = "soccer",
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO skipped_candidates (
                match_id, snapshot_id, sport, provider, strategy_version, reason, details_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (match_id, snapshot_id, sport, provider, strategy_version, reason, _json(details or {}), now_iso()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def insert_provider_health(self, health: ProviderHealth) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO provider_health (sport, provider, checked_at, ok, message, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (health.sport, health.provider, health.checked_at, int(health.ok), health.message, health.latency_ms),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def start_run(self, run_type: str, sport: str = "soccer") -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO run_metadata (sport, run_type, started_at, status, summary_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sport, run_type, now_iso(), "running", "{}"),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, summary: dict[str, Any]) -> None:
        self.conn.execute(
            """
            UPDATE run_metadata
            SET finished_at = ?, status = ?, summary_json = ?
            WHERE id = ?
            """,
            (now_iso(), status, _json(summary), run_id),
        )
        self.conn.commit()

    def open_signals(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT value_signals.*, matches.url, matches.home_team, matches.away_team
                     , matches.sport AS match_sport, matches.provider, matches.provider_match_id, matches.league, matches.start_time
                FROM value_signals
                JOIN matches ON matches.id = value_signals.match_id
                WHERE value_signals.status = 'open'
                ORDER BY value_signals.created_at
                """
            )
        )

    def insert_settlement(self, settlement: Settlement, raw: dict[str, Any] | None = None) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO settlements (
                signal_id, match_id, settled_at, final_home_score, final_away_score,
                result, payout_units, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                settlement.signal_id,
                settlement.match_id,
                settlement.settled_at,
                settlement.final_home_score,
                settlement.final_away_score,
                settlement.result,
                settlement.payout_units,
                _json(raw or {}),
            ),
        )
        self.conn.execute(
            "UPDATE value_signals SET status = 'settled' WHERE id = ?",
            (settlement.signal_id,),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def insert_historical_match(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO historical_matches (
                sport, source, division, season, match_date, home_team, away_team, fthg, ftag,
                hthg, htag, home_shots, away_shots, home_shots_target, away_shots_target,
                home_corners, away_corners, home_red_cards, away_red_cards, over25_odds,
                under25_odds, close_over25_odds, close_under25_odds, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("sport", "soccer"),
                row.get("source"),
                row.get("division"),
                row.get("season"),
                row.get("match_date"),
                row.get("home_team"),
                row.get("away_team"),
                row.get("fthg"),
                row.get("ftag"),
                row.get("hthg"),
                row.get("htag"),
                row.get("home_shots"),
                row.get("away_shots"),
                row.get("home_shots_target"),
                row.get("away_shots_target"),
                row.get("home_corners"),
                row.get("away_corners"),
                row.get("home_red_cards"),
                row.get("away_red_cards"),
                row.get("over25_odds"),
                row.get("under25_odds"),
                row.get("close_over25_odds"),
                row.get("close_under25_odds"),
                _json(row.get("raw", {})),
            ),
        )

    def insert_calibration_run(self, result: Any) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO calibration_runs (
                sport, strategy_version, source, source_url, season, status,
                sample_size, metrics_json, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.sport,
                result.strategy_version,
                result.source,
                result.source_url,
                result.season,
                result.status,
                result.sample_size,
                _json(result.metrics),
                result.notes,
                result.created_at,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def latest_calibration(self, sport: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM calibration_runs
            WHERE sport = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (sport,),
        ).fetchone()

    def latest_successful_calibration(self, sport: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT * FROM calibration_runs
            WHERE sport = ? AND status = 'calibrated_baseline'
            ORDER BY id DESC
            LIMIT 1
            """,
            (sport,),
        ).fetchone()

    def historical_matches(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM historical_matches
                WHERE fthg IS NOT NULL AND ftag IS NOT NULL
                ORDER BY match_date, id
                """
            )
        )

    def report_summary(self, sport: str | None = None) -> dict[str, Any]:
        query = self.conn.execute
        where = " WHERE sport = ?" if sport else ""
        params = (sport,) if sport else ()
        return {
            "matches": query(f"SELECT COUNT(*) AS n FROM matches{where}", params).fetchone()["n"],
            "snapshots": query(f"SELECT COUNT(*) AS n FROM live_snapshots{where}", params).fetchone()["n"],
            "signals": query(f"SELECT COUNT(*) AS n FROM value_signals{where}", params).fetchone()["n"],
            "open_signals": query(
                "SELECT COUNT(*) AS n FROM value_signals WHERE status='open'" + (" AND sport = ?" if sport else ""),
                params,
            ).fetchone()["n"],
            "settled_signals": query(
                "SELECT COUNT(*) AS n FROM value_signals WHERE status='settled'" + (" AND sport = ?" if sport else ""),
                params,
            ).fetchone()["n"],
            "skips": query(f"SELECT COUNT(*) AS n FROM skipped_candidates{where}", params).fetchone()["n"],
        }


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True)
