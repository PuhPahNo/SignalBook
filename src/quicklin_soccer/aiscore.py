from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import dataclass

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from quicklin_soccer.models import (
    HistoricalProfile,
    MatchRef,
    MatchStats,
    is_coherent_two_sided_odds,
    now_iso,
)
from quicklin_soccer.sports import SPORT_BASEBALL, SPORT_BASKETBALL, SPORT_HOCKEY, SPORT_SOCCER, SPORT_TENNIS, sport_config


AISCORE_HOME = "https://www.aiscore.com/"
STAT_CORNERS = "2"
STAT_SHOTS_ON_TARGET = "21"
STAT_SHOTS_OFF_TARGET = "22"
STAT_ATTACKS = "23"
STAT_DANGEROUS_ATTACKS = "24"
STAT_YELLOW_CARDS = "3"
STAT_RED_CARDS = "4"
CRITICAL_STATS = (
    STAT_CORNERS,
    STAT_SHOTS_ON_TARGET,
    STAT_SHOTS_OFF_TARGET,
    STAT_ATTACKS,
    STAT_DANGEROUS_ATTACKS,
)


class ScrapeError(RuntimeError):
    pass


class StatsUnavailable(ScrapeError):
    pass


@dataclass(frozen=True)
class BrowserConfig:
    # Default to headed mode — AIScore's bot check (deployed late May 2026)
    # serves an empty page to headless Chrome even with anti-detection flags.
    # The CLI's --headful flag is now effectively the default; pass
    # `headless=True` explicitly to opt back in.
    headless: bool = False
    wait_timeout: int = 25
    page_load_timeout: int = 35
    window_size: str = "1400,1200"


class AiScoreScraper:
    def __init__(self, config: BrowserConfig | None = None):
        self.config = config or BrowserConfig()
        self.driver = self._build_driver()
        self._listing_text_by_url: dict[str, str] = {}

    def __enter__(self) -> "AiScoreScraper":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        self.driver.quit()

    def live_match_urls(self, limit: int | None = None, max_scrolls: int = 30, sport: str = SPORT_SOCCER) -> list[str]:
        self.driver.get(sport_config(sport).aiscore_url)
        try:
            WebDriverWait(self.driver, self.config.wait_timeout).until(
                lambda driver: driver.execute_script(
                    "return document.querySelectorAll("
                    "'a.match-container, a[href*=\"/match-\"]').length > 0"
                )
            )
        except TimeoutException:
            # Genuinely empty slate (no live matches anywhere); fall back to
            # the old body-text wait so we still return cleanly with [].
            self._wait_for_body_text()
        self._accept_cookies_if_present()

        expected_count = self._displayed_live_count()
        seen: list[str] = []
        stable_scrolls = 0
        last_count = -1

        for _ in range(max_scrolls):
            for url in self._visible_match_links(sport):
                if url not in seen:
                    seen.append(url)

            stable_scrolls = stable_scrolls + 1 if len(seen) == last_count else 0
            last_count = len(seen)

            if limit and len(seen) >= limit:
                return seen[:limit]
            if expected_count and len(seen) >= expected_count:
                return seen[:expected_count]
            if stable_scrolls >= 4:
                break

            self.driver.execute_script("window.scrollBy(0, Math.floor(window.innerHeight * 0.9));")
            time.sleep(0.6)

        return seen[:limit] if limit else seen

    def match_stats(self, url: str, sport: str = SPORT_SOCCER) -> MatchStats:
        if sport != SPORT_SOCCER:
            return self._generic_match_stats(url, sport)
        self.driver.get(url)
        self._wait_for_nuxt("window.__NUXT__.state.football.detail.WebMatchData.match")
        self._accept_cookies_if_present()

        detail = self.driver.execute_script("return window.__NUXT__.state.football.detail")
        match = detail["WebMatchData"]["match"]
        stats = detail.get("stats", {}).get("items", {})
        missing_stats = [stat_id for stat_id in CRITICAL_STATS if stat_id not in stats]
        if missing_stats:
            raise StatsUnavailable("match stats unavailable")

        home_score = _score(match, "homeScores", 0)
        away_score = _score(match, "awayScores", 0)
        minute = self._current_minute(match)
        odds = _total_goals_odds(match) or self._desktop_total_goals_odds()

        return MatchStats(
            url=url,
            home_team=match["homeTeam"]["name"],
            away_team=match["awayTeam"]["name"],
            minute=minute,
            home_score=home_score,
            away_score=away_score,
            home_attacks=_stat(stats, STAT_ATTACKS, "home"),
            away_attacks=_stat(stats, STAT_ATTACKS, "away"),
            home_dangerous_attacks=_stat(stats, STAT_DANGEROUS_ATTACKS, "home"),
            away_dangerous_attacks=_stat(stats, STAT_DANGEROUS_ATTACKS, "away"),
            home_shots_on_target=_stat(stats, STAT_SHOTS_ON_TARGET, "home"),
            away_shots_on_target=_stat(stats, STAT_SHOTS_ON_TARGET, "away"),
            home_shots_off_target=_stat(stats, STAT_SHOTS_OFF_TARGET, "home"),
            away_shots_off_target=_stat(stats, STAT_SHOTS_OFF_TARGET, "away"),
            home_corners=_stat(stats, STAT_CORNERS, "home"),
            away_corners=_stat(stats, STAT_CORNERS, "away"),
            live_total_line=odds[0],
            live_over_odds=odds[1],
            live_under_odds=odds[2],
            provider_match_id=match.get("id"),
            league=(match.get("competition") or {}).get("name"),
            captured_at=now_iso(),
            home_red_cards=_stat(stats, STAT_RED_CARDS, "home"),
            away_red_cards=_stat(stats, STAT_RED_CARDS, "away"),
            sport=SPORT_SOCCER,
            phase=None,
            clock=None,
            stats={},
        )

    def match_ref(self, url: str, sport: str = SPORT_SOCCER) -> MatchRef:
        if sport != SPORT_SOCCER:
            return self._generic_match_ref(url, sport)
        self.driver.get(url)
        self._wait_for_nuxt("window.__NUXT__.state.football.detail.WebMatchData.match")
        detail = self.driver.execute_script("return window.__NUXT__.state.football.detail")
        match = detail["WebMatchData"]["match"]
        return MatchRef(
            provider="aiscore",
            provider_match_id=match["id"],
            url=url,
            home_team=match["homeTeam"]["name"],
            away_team=match["awayTeam"]["name"],
            league=(match.get("competition") or {}).get("name"),
            start_time=_match_start_iso(match),
            sport=SPORT_SOCCER,
        )

    def match_result(
        self,
        url: str,
        sport: str = SPORT_SOCCER,
        home_team: str | None = None,
        away_team: str | None = None,
    ) -> dict:
        if sport != SPORT_SOCCER:
            return self._generic_match_result(url, sport, home_team=home_team, away_team=away_team)
        self.driver.get(url)
        self._wait_for_nuxt("window.__NUXT__.state.football.detail.WebMatchData.match")
        detail = self.driver.execute_script("return window.__NUXT__.state.football.detail")
        match = detail["WebMatchData"]["match"]
        return {
            "provider_match_id": match.get("id"),
            "home_team": match["homeTeam"]["name"],
            "away_team": match["awayTeam"]["name"],
            "home_score": _score(match, "homeScores", 0),
            "away_score": _score(match, "awayScores", 0),
            "status_id": match.get("statusId"),
            "minute": self._current_minute(match),
            "is_final": _is_final_state(self._scoreboard_text(), SPORT_SOCCER, match.get("statusId")),
            "raw": match,
        }

    def historical_profile(self, match_url: str) -> HistoricalProfile:
        mobile_h2h_url = _mobile_h2h_url(match_url)
        self.driver.get(mobile_h2h_url)
        self._wait_for_nuxt('window.__NUXT__.state["football/detail"].HISTORY_DETAIL_DATA')
        state = self.driver.execute_script('return window.__NUXT__.state["football/detail"]')
        history_data = state.get("HISTORY_DETAIL_DATA") or {}

        matches = [
            *history_data.get("h2h", [])[:12],
            *history_data.get("home", [])[:24],
            *history_data.get("away", [])[:24],
        ]
        return HistoricalProfile(
            final_goal_totals=tuple(_goal_totals(matches, score_index=0)),
            halftime_goal_totals=tuple(_goal_totals(matches, score_index=1)),
        )

    def _build_driver(self):
        options = Options()
        options.add_argument("--disable-gpu")
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-notifications")
        # Anti-bot hardening: hide Selenium/Chromedriver fingerprints so
        # AIScore (and the Cloudflare layer in front of it) doesn't serve
        # us a stripped JS-stub page with no match elements.
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        )
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        if self.config.headless:
            options.add_argument("--headless=new")
            # Headless can use the full configured window size — there's no
            # visible window to hide.
            options.add_argument(f"--window-size={self.config.window_size}")
        else:
            # Headed but unobtrusive on macOS. --window-position with a
            # large negative value is silently clamped back to (0,0) by
            # macOS, so the offscreen trick alone doesn't hide anything.
            # We keep the launch window tiny (so the inevitable flash is
            # almost imperceptible) and then minimize_window() below to
            # send it to the Dock.
            options.add_argument("--window-size=400,400")
            options.add_argument("--window-position=0,0")

        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(self.config.page_load_timeout)
        # Send the launched window straight to the Dock so Anthony never
        # sees a Chrome window on top of his work. Wrapped in try/except
        # so a Selenium API hiccup doesn't take the whole scan down.
        if not self.config.headless:
            try:
                driver.minimize_window()
            except WebDriverException:
                pass
        # Hide navigator.webdriver before any page JS runs. AIScore's
        # bot check reads this property; if it returns true we get the
        # empty-match page that's been killing the scraper.
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(navigator, 'webdriver', "
                    "{get: () => undefined});"
                )
            },
        )
        return driver

    def _wait_for_body_text(self) -> None:
        WebDriverWait(self.driver, self.config.wait_timeout).until(
            lambda driver: len(driver.find_element(By.TAG_NAME, "body").text) > 200
        )

    def _wait_for_nuxt(self, expression: str) -> None:
        WebDriverWait(self.driver, self.config.wait_timeout).until(
            lambda driver: driver.execute_script(f"return !!({expression})")
        )

    def _accept_cookies_if_present(self) -> None:
        for text in ("Allow All", "Accept All", "Accept"):
            try:
                candidates = self.driver.find_elements(
                    By.XPATH,
                    f"//*[self::button or self::div or self::span][normalize-space()='{text}']",
                )
                if candidates:
                    candidates[0].click()
                    return
            except WebDriverException:
                return

    def _displayed_live_count(self) -> int | None:
        match = re.search(r"\bLive\s*\((\d+)\)", self.driver.find_element(By.TAG_NAME, "body").text)
        return int(match.group(1)) if match else None

    def _visible_match_links(self, sport: str = SPORT_SOCCER) -> list[str]:
        legacy_urls = self.driver.execute_script(
            """
            return [...document.querySelectorAll('a.match-container')]
                .map(link => link.href)
                .filter(href => href && href.includes('/match-'));
            """
        )
        if legacy_urls:
            return list(dict.fromkeys(legacy_urls))

        links = self.driver.execute_script(
            """
            return [...document.querySelectorAll('a[href*="/match-"]')].map(link => ({
                href: link.href,
                text: link.innerText || ""
            }));
            """
        )
        urls = [
            item["href"]
            for item in links
            if _is_live_match_link(str(item.get("href") or ""), str(item.get("text") or ""), sport)
        ]
        for item in links:
            href = str(item.get("href") or "")
            text = str(item.get("text") or "")
            if href in urls:
                self._listing_text_by_url[href] = text
        return list(dict.fromkeys(urls))

    def _desktop_total_goals_odds(self) -> tuple[float | None, float | None, float | None]:
        body_text = self.driver.find_element(By.TAG_NAME, "body").text
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        try:
            start = lines.index("Goals") + 1
            end = lines.index("Corners", start)
        except ValueError:
            return None, None, None

        tokens = [line for line in lines[start:end] if line not in {"Over", "Under"}]
        triples = []
        for index in range(0, len(tokens) - 2, 3):
            line = _float_line(tokens[index])
            over = _float(tokens[index + 1])
            under = _float(tokens[index + 2])
            if line is not None and over is not None and under is not None:
                triples.append((line, over, under))

        return triples[-1] if triples else (None, None, None)

    def _current_minute(self, match: dict) -> int:
        text = self._scoreboard_text()
        parsed = _parse_minute(text)
        if parsed is not None:
            return parsed

        server_time = self.driver.execute_script("return window.__NUXT__.state.serverTime")
        match_time = match.get("matchTime")
        if isinstance(server_time, int) and isinstance(match_time, int):
            return max(1, int((server_time - match_time) / 60))
        raise ScrapeError("Could not determine match minute")

    def _scoreboard_text(self) -> str:
        try:
            return self.driver.find_element(By.CSS_SELECTOR, ".scoreBox").text
        except WebDriverException:
            return ""

    def _generic_match_ref(self, url: str, sport: str) -> MatchRef:
        self.driver.get(url)
        self._wait_for_body_text()
        self._accept_cookies_if_present()
        match = self._generic_match_payload()
        listing = _parse_listing_match(self._listing_text_by_url.get(url, ""), sport)
        return MatchRef(
            provider="aiscore",
            provider_match_id=_generic_provider_match_id(url),
            url=url,
            home_team=_fallback_value(_team_name(match, "homeTeam"), listing.get("home_team"), "Unknown"),
            away_team=_fallback_value(_team_name(match, "awayTeam"), listing.get("away_team"), "Unknown"),
            league=(match.get("competition") or {}).get("name"),
            start_time=_match_start_iso(match),
            sport=sport,
        )

    def _generic_match_stats(self, url: str, sport: str) -> MatchStats:
        self.driver.get(url)
        self._wait_for_body_text()
        self._accept_cookies_if_present()
        match = self._generic_match_payload()
        body_text = self.driver.find_element(By.TAG_NAME, "body").text
        listing = _parse_listing_match(self._listing_text_by_url.get(url, ""), sport)
        phase, clock, minute = _generic_time_state(body_text, sport)
        phase = listing.get("phase") or phase
        clock = listing.get("clock") or clock
        minute = listing.get("minute") or minute
        odds = _generic_total_odds(match)
        if odds == (None, None, None):
            # Prefer the detail page's in-play total (authoritative, coherent)
            # over the listing row, which mis-pairs numbers into impossible
            # books. Listing stays as a last resort only.
            odds = _parse_detail_total_odds(body_text, sport) or listing.get("odds") or odds
        stats = _generic_stats(match, phase, clock)
        return MatchStats(
            url=url,
            home_team=_fallback_value(_team_name(match, "homeTeam"), listing.get("home_team"), "Unknown"),
            away_team=_fallback_value(_team_name(match, "awayTeam"), listing.get("away_team"), "Unknown"),
            minute=minute,
            home_score=_fallback_number(_generic_score(match, "home"), listing.get("home_score")),
            away_score=_fallback_number(_generic_score(match, "away"), listing.get("away_score")),
            home_attacks=0,
            away_attacks=0,
            home_dangerous_attacks=0,
            away_dangerous_attacks=0,
            home_shots_on_target=_generic_stat(stats, "home_shots_on_goal"),
            away_shots_on_target=_generic_stat(stats, "away_shots_on_goal"),
            home_shots_off_target=0,
            away_shots_off_target=0,
            home_corners=0,
            away_corners=0,
            live_total_line=odds[0],
            live_over_odds=odds[1],
            live_under_odds=odds[2],
            provider_match_id=_generic_provider_match_id(url),
            league=(match.get("competition") or {}).get("name"),
            captured_at=now_iso(),
            sport=sport,
            phase=phase,
            clock=clock,
            stats=stats,
        )

    def _generic_match_result(
        self,
        url: str,
        sport: str,
        home_team: str | None = None,
        away_team: str | None = None,
    ) -> dict:
        self.driver.get(url)
        self._wait_for_body_text()
        match = self._generic_match_payload()
        body_text = self.driver.find_element(By.TAG_NAME, "body").text
        listing = _parse_listing_match(self._listing_text_by_url.get(url, ""), sport)
        phase, clock, minute = _generic_time_state(body_text, sport)
        phase = listing.get("phase") or phase
        clock = listing.get("clock") or clock
        minute = listing.get("minute") or minute
        detail_score = _parse_detail_scoreboard(body_text, home_team, away_team, sport)
        score_source = "detail_scoreboard" if detail_score else "nuxt_match"
        home_score = _fallback_number(_generic_score(match, "home"), listing.get("home_score"))
        away_score = _fallback_number(_generic_score(match, "away"), listing.get("away_score"))
        if detail_score:
            home_score, away_score = detail_score
        return {
            "provider_match_id": _generic_provider_match_id(url),
            "home_team": _fallback_value(_team_name(match, "homeTeam"), listing.get("home_team") or home_team, "Unknown"),
            "away_team": _fallback_value(_team_name(match, "awayTeam"), listing.get("away_team") or away_team, "Unknown"),
            "home_score": home_score,
            "away_score": away_score,
            "status_id": match.get("statusId"),
            "minute": minute,
            "phase": phase,
            "clock": clock,
            "sport": sport,
            "is_final": _is_final_state(body_text, sport, match.get("statusId")),
            "score_source": score_source,
            "raw": match,
            # Kept transiently so the settle pass can dump it for matches that
            # fail to settle (non-soccer, no detail scoreboard). This is the
            # only place the raw detail-page text is available; nothing
            # persists it, which is why the tennis/baseball parser couldn't be
            # rebuilt offline. Not stored unless --capture-dir is passed.
            "page_text": body_text,
        }

    def _generic_match_payload(self) -> dict:
        script = """
            const root = window.__NUXT__ && window.__NUXT__.state;
            const seen = new Set();
            function findMatch(value) {
                if (!value || typeof value !== 'object' || seen.has(value)) return null;
                seen.add(value);
                if (value.homeTeam && value.awayTeam && (value.id || value.matchId || value.matchTime)) return value;
                if (Array.isArray(value)) {
                    for (const item of value) {
                        const found = findMatch(item);
                        if (found) return found;
                    }
                    return null;
                }
                for (const key of Object.keys(value)) {
                    const found = findMatch(value[key]);
                    if (found) return found;
                }
                return null;
            }
            return findMatch(root);
        """
        match = self.driver.execute_script(script)
        if not match:
            raise StatsUnavailable("match payload unavailable")
        return match


def _mobile_h2h_url(match_url: str) -> str:
    url = match_url.replace("https://www.", "https://m.")
    if not url.endswith("/h2h"):
        url = f"{url.rstrip('/')}/h2h"
    return url


def _generic_provider_match_id(url: str) -> str:
    return url.rstrip("/").removesuffix("/h2h")


def _is_final_state(text: str, sport: str, status_id=None) -> bool:
    normalized = text.replace("\n", " ")
    # Unambiguous final markers. These never appear in AiScore's page
    # boilerplate, so they're authoritative even when the scoreboard still
    # shows set/inning labels that _has_live_state would otherwise read as
    # live — the exact bug that kept finished tennis and baseball matches
    # from ever settling.
    strong_final = ("Full Time", "Finished", "Ended", "After Penalties", "AET")
    if any(re.search(rf"\b{re.escape(token)}\b", normalized, re.IGNORECASE) for token in strong_final):
        return True
    if status_id in {8, 10, 11, 12, 13}:
        return True
    # No explicit final marker: if the page still shows a live clock/period,
    # it's in progress. This guards the weak "Final" token below against the
    # "...halftime or final result." boilerplate on every live page.
    if sport != SPORT_SOCCER and _has_live_state(text, sport):
        return False
    weak_final = ("FT", "Final")
    return any(re.search(rf"\b{re.escape(token)}\b", normalized, re.IGNORECASE) for token in weak_final)


def _has_live_state(text: str, sport: str) -> bool:
    normalized = text.replace("\n", " ")
    if sport == SPORT_HOCKEY:
        return bool(re.search(r"\b(P[1-3]|OT|SO)\s*[- ]\s*\d{1,2}:\d{2}\b", normalized))
    if sport == SPORT_BASKETBALL:
        return bool(re.search(r"\b(Q[1-4]|OT)\s*[- ]\s*\d{1,2}:\d{2}\b", normalized))
    if sport == SPORT_BASEBALL:
        return bool(re.search(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+Inning|Top\s+\d{1,2}|Bottom\s+\d{1,2})\b", normalized, re.IGNORECASE))
    if sport == SPORT_TENNIS:
        return bool(re.search(r"\bS[1-5]\b", normalized))
    return False


def _match_start_iso(match: dict) -> str | None:
    match_time = match.get("matchTime")
    if not isinstance(match_time, int):
        return None
    from datetime import UTC, datetime

    return datetime.fromtimestamp(match_time, UTC).isoformat(timespec="seconds")


def _parse_minute(text: str) -> int | None:
    normalized = text.replace("\n", " ").strip()
    if not normalized:
        return None
    if any(token in normalized for token in ("FT", "Full Time", "Penalties")):
        return 90
    if any(token in normalized for token in ("HT", "Half Time")):
        return 45
    match = re.search(r"(\d{1,3})(?:\+)?\s*'?", normalized)
    if not match:
        return None
    return min(int(match.group(1)), 90)


def _is_live_match_link(href: str, text: str, sport: str) -> bool:
    if "/match-" not in href or "/h2h" in href:
        return False
    normalized = text.replace("\n", " ")
    if sport == SPORT_HOCKEY:
        return bool(re.search(r"\b(P[1-3]|OT|SO)\b", normalized))
    if sport == SPORT_BASKETBALL:
        return bool(re.search(r"\b(Q[1-4]|OT)\b", normalized))
    if sport == SPORT_BASEBALL:
        return bool(re.search(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+Inning|Top\s+\d{1,2}|Bottom\s+\d{1,2})\b", normalized, re.IGNORECASE))
    if sport == SPORT_TENNIS:
        return bool(re.search(r"\bS[1-5]\b", normalized))
    return _parse_minute(normalized) is not None


def _parse_listing_match(text: str, sport: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {}
    phase_index = _listing_phase_index(lines, sport)
    if phase_index is None:
        return {}

    phase_text = lines[phase_index]
    phase, clock, minute = _generic_time_state(phase_text, sport)
    home_index = phase_index + 1
    if sport == SPORT_BASKETBALL and home_index < len(lines) and lines[home_index] == "-":
        home_index += 1
    if sport == SPORT_BASKETBALL and home_index < len(lines) and re.fullmatch(r"\d{1,2}:\d{2}", lines[home_index]):
        clock = lines[home_index]
        phase, _, minute = _generic_time_state(f"{phase or phase_text} {clock}", sport)
        home_index += 1
    if home_index + 1 >= len(lines):
        return {}

    home_team = lines[home_index]
    away_team = lines[home_index + 1]
    home_score, away_score = _listing_scores(lines[home_index + 2 :], sport, phase)
    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "phase": phase,
        "clock": clock,
        "minute": minute,
        "odds": _parse_listing_total_odds(lines),
    }


def _parse_detail_scoreboard(
    text: str,
    home_team: str | None,
    away_team: str | None,
    sport: str,
) -> tuple[int, int] | None:
    # Tennis is special: the top scoreboard shows SETS won, but the bet is on
    # total games. Sum the per-set summaries instead. See _tennis_total_games.
    if sport == SPORT_TENNIS:
        return _tennis_total_games(text)
    if not home_team or not away_team:
        return None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    home_key = _normalize_team(home_team)
    away_key = _normalize_team(away_team)
    for index, line in enumerate(lines[:-4]):
        if _normalize_team(line) != home_key:
            continue
        if _normalize_team(lines[index + 4]) != away_key:
            continue
        home_score = _int(lines[index + 1])
        away_score = _int(lines[index + 3])
        phase_line = lines[index + 2]
        # "Full" matches the "Full Time" label AiScore shows on a finished
        # match — without it, completed games were rejected and never settled.
        if sport == SPORT_HOCKEY and not re.search(r"\b(P[1-3]|OT|SO|Full|FT|Final|Finished)\b", phase_line, re.IGNORECASE):
            continue
        if sport == SPORT_BASKETBALL and not re.search(r"\b(Q[1-4]|OT|Full|FT|Final|Finished)\b", phase_line, re.IGNORECASE):
            continue
        if sport == SPORT_BASEBALL and not re.search(r"\b(Inning|Top|Bottom|Full|FT|Final|Finished)\b", phase_line, re.IGNORECASE):
            continue
        return home_score, away_score
    return None


def _tennis_total_games(text: str) -> tuple[int, int] | None:
    """Total games per player for a finished tennis match, summed from the
    per-set summaries AiScore prints (e.g. ``S1, 2-6``). The headline
    scoreboard only shows sets won (2-1), but tennis totals settle on games,
    so we sum every set's score. Order follows the scoreboard (home first);
    for a totals bet only the sum matters. Returns None when no completed-set
    summaries are present (e.g. a retirement), so the match is left unsettled
    rather than graded on a partial score."""
    sets = re.findall(r"\bS[1-5],\s*(\d+)\s*-\s*(\d+)", text)
    if not sets:
        return None
    home = sum(int(home_games) for home_games, _ in sets)
    away = sum(int(away_games) for _, away_games in sets)
    return home, away


def _listing_phase_index(lines: list[str], sport: str) -> int | None:
    for index, line in enumerate(lines):
        if sport == SPORT_HOCKEY and re.search(r"\b(P[1-3]|OT|SO)\b", line):
            return index
        if sport == SPORT_BASKETBALL and re.search(r"\b(Q[1-4]|OT)\b", line):
            return index
        if sport == SPORT_BASEBALL and re.search(r"\b(\d{1,2}(?:st|nd|rd|th)?\s+Inning|Top\s+\d{1,2}|Bottom\s+\d{1,2})\b", line, re.IGNORECASE):
            return index
        if sport == SPORT_TENNIS and re.search(r"\bS[1-5]\b", line):
            return index
    return None


def _listing_scores(lines: list[str], sport: str, phase: str | None) -> tuple[int | None, int | None]:
    numbers = [_int(line) for line in lines if re.fullmatch(r"\d+", line)]
    if len(numbers) < 2:
        return None, None
    if sport in {SPORT_HOCKEY, SPORT_BASKETBALL}:
        period_count = _period_count(phase, default=1)
        away_index = 1 + period_count
        if len(numbers) > away_index:
            return numbers[0], numbers[away_index]
    return numbers[0], numbers[1]


def _period_count(phase: str | None, default: int = 1) -> int:
    if not phase:
        return default
    match = re.search(r"(\d+)", phase)
    return _int(match.group(1)) if match else default


def _parse_detail_total_odds(text: str, sport: str) -> tuple[float, float, float] | None:
    """Parse the in-play total market straight off the match detail page, e.g.
    ``Total Runs / 12.5 / 1.86 / 1.80``. This is the authoritative source: the
    compact listing row was mis-pairing numbers into incoherent books (implied
    probabilities summing to ~0.70), which manufactured huge phantom EV and even
    made the bot bet both sides of the same line.

    The page lists ``<label>`` then repeating ``<line> <over> <under>`` triples
    (a suspended/absent market shows dashes). We return the first coherent
    triple — both odds > 1 and implied probabilities summing to ~>= 1 — or None
    when the market isn't really offered. The coherence check also rejects any
    accidental misalignment, since a wrong pairing won't sum to a real book."""
    label = sport_config(sport).total_market.replace("_", " ").lower()  # e.g. "total runs"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.lower() != label:
            continue
        # Primary triple sits right after the label; a secondary line follows.
        for base in (index + 1, index + 4):
            triple = lines[base:base + 3]
            if len(triple) < 3:
                continue
            line_val = _float(triple[0])
            over = _float(triple[1])
            under = _float(triple[2])
            if line_val is None or not is_coherent_two_sided_odds(over, under):
                continue
            return line_val, over, under
        return None
    return None


def _parse_listing_total_odds(lines: list[str]) -> tuple[float | None, float | None, float | None] | None:
    for index, line in enumerate(lines):
        match = re.match(r"^O\s+([0-9]+(?:\.[0-9]+)?)$", line, re.IGNORECASE)
        if not match:
            continue
        total_line = _float(match.group(1))
        odds = []
        for candidate in lines[index + 1 : index + 7]:
            if re.match(r"^[UO]\s+", candidate, re.IGNORECASE):
                continue
            value = _float(candidate)
            if value is not None:
                odds.append(value)
            if len(odds) == 2:
                return total_line, odds[0], odds[1]
    return None


def _fallback_value(primary: str, fallback, missing: str) -> str:
    return str(fallback) if primary == missing and fallback else primary


def _fallback_number(primary: int, fallback) -> int:
    if fallback is None:
        return primary
    return _int(fallback)


def _score(match: dict, key: str, index: int) -> int:
    scores = match.get(key) or []
    if len(scores) <= index:
        return 0
    return _int(scores[index])


def _team_name(match: dict, key: str) -> str:
    team = match.get(key) or {}
    return str(team.get("name") or team.get("shortName") or "Unknown")


def _normalize_team(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _generic_score(match: dict, side: str) -> int:
    direct_keys = {
        "home": ("homeScore", "home_score", "homeTotalScore"),
        "away": ("awayScore", "away_score", "awayTotalScore"),
    }
    for key in direct_keys[side]:
        if key in match:
            return _int(match.get(key))

    scores = match.get(f"{side}Scores") or []
    if not scores:
        return 0
    first = _int(scores[0])
    if first:
        return first
    values = [_int(value) for value in scores]
    return max(values) if values else 0


def _generic_time_state(body_text: str, sport: str) -> tuple[str | None, str | None, int]:
    normalized = body_text.replace("\n", " ")
    if sport == "hockey":
        match = re.search(r"\b(P[1-3]|OT|SO)\s*[- ]\s*(\d{1,2}:\d{2})", normalized)
        if match:
            period = match.group(1)
            clock = match.group(2)
            period_number = _int(period[1:]) if period.startswith("P") else 4
            return period, clock, _elapsed_from_clock(period_number, clock, 20)
    if sport == "basketball":
        match = re.search(r"\b(Q[1-4]|OT)\s*[- ]\s*(\d{1,2}:\d{2})", normalized)
        if match:
            quarter = match.group(1)
            clock = match.group(2)
            quarter_number = _int(quarter[1:]) if quarter.startswith("Q") else 5
            return quarter, clock, _elapsed_from_clock(quarter_number, clock, 12)
    if sport == "baseball":
        match = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+Inning\b", normalized, re.IGNORECASE)
        if match:
            inning = _int(match.group(1))
            return f"inning_{inning}", None, max(1, inning)
        match = re.search(r"\b(Top|Bottom)\s+(\d{1,2})(?:st|nd|rd|th)?", normalized, re.IGNORECASE)
        if match:
            inning = _int(match.group(2))
            return f"{match.group(1).lower()}_{inning}", None, max(1, inning)
    if sport == "tennis":
        match = re.search(r"\bSet\s*(\d)", normalized, re.IGNORECASE)
        if match:
            return f"set_{match.group(1)}", None, _int(match.group(1))
    return None, None, 1


def _elapsed_from_clock(period_number: int, clock: str, period_minutes: int) -> int:
    try:
        minutes, seconds = [int(part) for part in clock.split(":", 1)]
    except ValueError:
        return max(1, (period_number - 1) * period_minutes)
    remaining = minutes + (1 if seconds else 0)
    return max(1, ((period_number - 1) * period_minutes) + max(0, period_minutes - remaining))


def _generic_stats(match: dict, phase: str | None, clock: str | None) -> dict:
    stats = {"phase": phase, "clock": clock}
    raw_stats = match.get("stats") or match.get("statistics") or {}
    if isinstance(raw_stats, dict):
        stats["raw_stats"] = raw_stats
    return stats


def _generic_stat(stats: dict, key: str) -> int:
    return _int(stats.get(key))


def _stat(stats: dict, stat_id: str, side: str) -> int:
    return _int((stats.get(stat_id) or {}).get(side))


def _int(value) -> int:
    try:
        return int(float(str(value).strip().replace("%", "")))
    except (TypeError, ValueError):
        return 0


def _float(value) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _total_goals_odds(match: dict) -> tuple[float | None, float | None, float | None] | None:
    odd_items = (((match.get("ext") or {}).get("odds") or {}).get("oddItems") or [])
    if len(odd_items) < 3:
        return None

    total_goal_odds = odd_items[2].get("odd") if odd_items[2] else None
    if not total_goal_odds or len(total_goal_odds) < 3:
        return None

    over_odds = _float(total_goal_odds[0])
    line = _float_line(total_goal_odds[1])
    under_odds = _float(total_goal_odds[2])
    return line, over_odds, under_odds


def _generic_total_odds(match: dict) -> tuple[float | None, float | None, float | None]:
    odds = _total_goals_odds(match)
    if odds:
        return odds
    return None, None, None


def _float_line(value) -> float | None:
    text = str(value).strip()
    if "/" in text:
        parts = [_float(part) for part in text.split("/", 1)]
        if all(part is not None for part in parts):
            return sum(parts) / len(parts)
    return _float(text)


def _goal_totals(matches: Iterable[dict], score_index: int) -> list[int]:
    totals: list[int] = []
    for match in matches:
        home_score = _score(match, "homeScores", score_index)
        away_score = _score(match, "awayScores", score_index)
        if home_score == 0 and away_score == 0 and score_index == 1:
            # Keep scoreless halftime results; they matter for under signals.
            totals.append(0)
        else:
            totals.append(home_score + away_score)
    return totals


def classify_error(exc: Exception) -> str:
    if isinstance(exc, TimeoutException):
        return "timed out waiting for page data"
    if isinstance(exc, ScrapeError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
