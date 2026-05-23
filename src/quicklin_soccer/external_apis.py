from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ApiResponse:
    ok: bool
    provider: str
    status_code: int | None
    data: dict[str, Any] | None
    message: str


class TheOddsApiClient:
    provider = "the_odds_api"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("THE_ODDS_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def soccer_odds(self, sport_key: str = "soccer_epl", regions: str = "us,uk", markets: str = "totals") -> ApiResponse:
        if not self.api_key:
            return ApiResponse(False, self.provider, None, None, "THE_ODDS_API_KEY not set")
        params = urllib.parse.urlencode(
            {"apiKey": self.api_key, "regions": regions, "markets": markets, "oddsFormat": "decimal"}
        )
        return _get_json(f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?{params}", self.provider)


class ApiFootballClient:
    provider = "api_football"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("API_FOOTBALL_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def live_fixtures(self) -> ApiResponse:
        if not self.api_key:
            return ApiResponse(False, self.provider, None, None, "API_FOOTBALL_KEY not set")
        return _get_json(
            "https://v3.football.api-sports.io/fixtures?live=all",
            self.provider,
            headers={"x-apisports-key": self.api_key},
        )


def _get_json(url: str, provider: str, headers: dict[str, str] | None = None) -> ApiResponse:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return ApiResponse(True, provider, response.status, payload, "ok")
    except Exception as exc:
        return ApiResponse(False, provider, None, None, f"{type(exc).__name__}: {exc}")
