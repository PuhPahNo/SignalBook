from __future__ import annotations

import json
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from quicklin_soccer.storage import DEFAULT_DB_PATH
from quicklin_soccer.sports import SPORT_SOCCER, SUPPORTED_SPORTS, default_strategy_for_sport
from quicklin_soccer.strategy_registry import strategy_metadata
from quicklin_soccer.web.jobs import JobRunner
from quicklin_soccer.web.read_models import (
    calibrations_query,
    performance_query,
    provider_health_query,
    query_payload,
    runs_query,
    settlements_query,
    signals_query,
    skips_query,
    snapshots_query,
    summary_payload,
)


STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


def run_server(host: str = "127.0.0.1", port: int = 8765, db_path: Path = DEFAULT_DB_PATH) -> None:
    runner = JobRunner()

    class Handler(QuicklinHandler):
        app_db_path = Path(db_path)
        app_runner = runner

    server = ThreadingHTTPServer((host, port), Handler)
    runner.start("bot", _default_bot_args(Path(db_path)), timeout_seconds=None)
    print(f"SignalBook web console running at http://{host}:{port}")
    print("SignalBook bot started automatically. Use the dashboard Run/Stop button to pause it.")
    try:
        server.serve_forever()
    finally:
        runner.cancel_kind("bot")
        server.server_close()


class QuicklinHandler(BaseHTTPRequestHandler):
    app_db_path: Path
    app_runner: JobRunner

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        sport = _query_sport(parsed.query)
        payload = self._api_get(path, sport)
        if payload is not None:
            self._json(payload)
            return
        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            self._static(filename, content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        body = self._read_json()
        if path == "/api/scan":
            job = self.app_runner.start("scan", self._scan_args(body))
            self._json({"job": job.as_dict()})
            return
        if path == "/api/monitor":
            job = self.app_runner.start("monitor", self._monitor_args(body), timeout_seconds=_monitor_timeout(body))
            self._json({"job": job.as_dict()})
            return
        if path == "/api/bot":
            running = self.app_runner.running("bot")
            if running:
                self._json({"job": running[0].as_dict(), "already_running": True})
                return
            job = self.app_runner.start("bot", self._bot_args(body), timeout_seconds=None)
            self._json({"job": job.as_dict(), "already_running": False})
            return
        if path == "/api/bot/cancel":
            self._json({"jobs": self.app_runner.cancel_kind("bot")})
            return
        if path == "/api/jobs/cancel":
            job = self.app_runner.cancel(_bounded_int(body.get("job_id"), default=0, minimum=0, maximum=999999))
            self._json({"job": job})
            return
        if path == "/api/settle":
            job = self.app_runner.start("settle", self._settle_args(body))
            self._json({"job": job.as_dict()})
            return
        if path == "/api/calibrate":
            job = self.app_runner.start("calibrate", self._calibrate_args(body))
            self._json({"job": job.as_dict()})
            return
        if path == "/api/report":
            job = self.app_runner.start("report", self._report_args())
            self._json({"job": job.as_dict()})
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args) -> None:
        return

    def _api_get(self, path: str, sport: str | None) -> dict[str, Any] | None:
        if path == "/api/summary":
            return summary_payload(self.app_db_path, sport)
        if path == "/api/signals":
            return query_payload(self.app_db_path, signals_query(sport), sport)
        if path == "/api/snapshots":
            return query_payload(self.app_db_path, snapshots_query(sport), sport)
        if path == "/api/skips":
            return query_payload(self.app_db_path, skips_query(sport), sport)
        if path == "/api/settlements":
            return query_payload(self.app_db_path, settlements_query(sport), sport)
        if path == "/api/performance":
            return query_payload(self.app_db_path, performance_query(sport), sport)
        if path == "/api/provider-health":
            return query_payload(self.app_db_path, provider_health_query(sport), sport)
        if path == "/api/calibrations":
            return query_payload(self.app_db_path, calibrations_query(sport), sport)
        if path == "/api/runs":
            return query_payload(self.app_db_path, runs_query(sport), sport)
        if path == "/api/jobs":
            return {"jobs": self.app_runner.jobs()}
        if path == "/api/strategies":
            return {"sports": strategy_metadata()}
        return None

    def _scan_args(self, body: dict[str, Any]) -> list[str]:
        sport = _body_sport(body)
        return [
            "scan",
            "--db",
            str(self.app_db_path),
            "--sport",
            sport,
            "--limit",
            str(_bounded_int(body.get("limit"), default=10, minimum=1, maximum=200)),
            "--strategy",
            str(body.get("strategy") or default_strategy_for_sport(sport)),
            "--min-ev",
            str(_bounded_float(body.get("min_ev"), default=0.03, minimum=0, maximum=1)),
            "--include-skipped",
            "--json",
        ]

    def _monitor_args(self, body: dict[str, Any]) -> list[str]:
        sport = _body_sport(body)
        args = [
            "monitor",
            "--db",
            str(self.app_db_path),
            "--sport",
            sport,
            "--limit",
            str(_bounded_int(body.get("limit"), default=10, minimum=1, maximum=200)),
            "--strategy",
            str(body.get("strategy") or default_strategy_for_sport(sport)),
            "--interval-seconds",
            str(_bounded_int(body.get("interval_seconds"), default=60, minimum=5, maximum=3600)),
            "--json",
        ]
        if not body.get("continuous"):
            args.extend(["--loops", str(_bounded_int(body.get("loops"), default=5, minimum=1, maximum=100))])
        return args

    def _bot_args(self, body: dict[str, Any]) -> list[str]:
        args = [
            "bot",
            "--db",
            str(self.app_db_path),
            "--sports",
            str(body.get("sports") or "all"),
            "--limit",
            str(_bounded_int(body.get("limit"), default=10, minimum=1, maximum=200)),
            "--scan-interval-seconds",
            str(_bounded_int(body.get("interval_seconds"), default=60, minimum=15, maximum=3600)),
            "--settle-interval-seconds",
            str(_bounded_int(body.get("settle_interval_seconds"), default=300, minimum=60, maximum=86400)),
            "--min-ev",
            str(_bounded_float(body.get("min_ev"), default=0.03, minimum=0, maximum=1)),
            "--include-skipped",
        ]
        return args

    def _settle_args(self, body: dict[str, Any]) -> list[str]:
        args = ["settle", "--db", str(self.app_db_path), "--json"]
        if body.get("force"):
            args.append("--force")
        return args

    def _calibrate_args(self, body: dict[str, Any]) -> list[str]:
        return ["calibrate", "--db", str(self.app_db_path), "--sport", _body_sport(body), "--json"]

    def _report_args(self) -> list[str]:
        return ["report", "--db", str(self.app_db_path), "--output-dir", "output/report", "--json"]

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _json(self, payload: dict[str, Any]) -> None:
        self._write(json.dumps(payload, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIR / filename
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        self._write(path.read_bytes(), content_type)

    def _write(self, data: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _query_sport(query: str) -> str | None:
    values = urllib.parse.parse_qs(query).get("sport", [])
    if not values:
        return None
    return values[0] if values[0] in SUPPORTED_SPORTS else SPORT_SOCCER


def _body_sport(body: dict[str, Any]) -> str:
    sport = str(body.get("sport") or SPORT_SOCCER)
    return sport if sport in SUPPORTED_SPORTS else SPORT_SOCCER


def _default_bot_args(db_path: Path) -> list[str]:
    return [
        "bot",
        "--db",
        str(db_path),
        "--sports",
        "all",
        "--limit",
        "10",
        "--scan-interval-seconds",
        "60",
        "--settle-interval-seconds",
        "300",
    ]


def _monitor_timeout(body: dict[str, Any]) -> int | None:
    if body.get("continuous"):
        return None
    loops = _bounded_int(body.get("loops"), default=5, minimum=1, maximum=100)
    interval = _bounded_int(body.get("interval_seconds"), default=60, minimum=5, maximum=3600)
    return max(180, (loops * interval) + 120)
