"""
Client for api-sports.io / API-Football v3.
Free tier: 100 requests/day, includes goal scorers, cards, lineups.

Sign up: https://rapidapi.com/api-sports/api/api-football
Set API_FOOTBALL_KEY in .env
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://v3.football.api-sports.io"
# FIFA World Cup 2026 league ID on API-Football
WC_LEAGUE_ID = 1
WC_SEASON = 2026


def _headers():
    return {
        "x-apisports-key": os.getenv("API_FOOTBALL_KEY", ""),
    }


def _get(endpoint: str, params: dict = None) -> dict:
    r = requests.get(f"{BASE_URL}/{endpoint}", headers=_headers(), params=params or {}, timeout=15)
    r.raise_for_status()
    return r.json()


# ─── Match fetching ────────────────────────────────────────────────────────────

def get_matches(status: str = None) -> list[dict]:
    """
    Fetch World Cup fixtures.
    status: NS (not started) | LIVE | FT (finished) | HT | 1H | 2H
    """
    params = {"league": WC_LEAGUE_ID, "season": WC_SEASON}
    if status:
        params["status"] = status
    data = _get("fixtures", params)
    return data.get("response", [])


def get_match(fixture_id: int) -> dict:
    """Get full fixture detail including events (goals, cards) and lineups."""
    data = _get("fixtures", {"id": fixture_id})
    fixtures = data.get("response", [])
    return fixtures[0] if fixtures else {}


def get_fixture_events(fixture_id: int) -> list[dict]:
    """Get all events (goals, cards, subs) for a fixture."""
    data = _get("fixtures/events", {"fixture": fixture_id})
    return data.get("response", [])


def get_live_matches() -> list[dict]:
    """Return matches currently in play."""
    return get_matches(status="LIVE") + get_matches(status="1H") + get_matches(status="2H") + get_matches(status="HT")


def get_standings() -> list[dict]:
    """Get group stage standings."""
    data = _get("standings", {"league": WC_LEAGUE_ID, "season": WC_SEASON})
    resp = data.get("response", [])
    return resp[0].get("league", {}).get("standings", []) if resp else []


def get_top_scorers() -> list[dict]:
    """Get top scorers for the tournament."""
    data = _get("players/topscorers", {"league": WC_LEAGUE_ID, "season": WC_SEASON})
    return data.get("response", [])


# ─── Normalization ─────────────────────────────────────────────────────────────

def _status_map(short: str) -> str:
    """Map API-Football short status to our internal status."""
    return {
        "NS": "SCHEDULED",
        "TBD": "SCHEDULED",
        "1H": "LIVE",
        "2H": "LIVE",
        "HT": "LIVE",
        "ET": "LIVE",
        "P": "LIVE",
        "FT": "FINISHED",
        "AET": "FINISHED",
        "PEN": "FINISHED",
        "PST": "POSTPONED",
        "CANC": "POSTPONED",
        "ABD": "POSTPONED",
    }.get(short, short)


def normalize_match_event(fixture: dict, events: list[dict] = None) -> dict:
    """Flatten an API-Football fixture + events into a clean ES document."""
    fix = fixture.get("fixture", {})
    teams = fixture.get("teams", {})
    goals_score = fixture.get("goals", {})
    score = fixture.get("score", {})
    league = fixture.get("league", {})

    home_name = teams.get("home", {}).get("name", "")
    away_name = teams.get("away", {}).get("name", "")

    goals = []
    cards = []
    subs = []

    for ev in (events or []):
        team_name = ev.get("team", {}).get("name", "")
        player = ev.get("player", {}).get("name")
        assist = ev.get("assist", {}).get("name")
        minute = ev.get("time", {}).get("elapsed")
        ev_type = ev.get("type", "")
        detail = ev.get("detail", "")

        if ev_type == "Goal" and detail != "Missed Penalty":
            goals.append({
                "minute": minute,
                "team": team_name,
                "scorer": player,
                "assist": assist,
                "type": detail,
            })
        elif ev_type == "Card":
            cards.append({
                "minute": minute,
                "team": team_name,
                "player": player,
                "card": detail,
            })
        elif ev_type == "subst":
            subs.append({
                "minute": minute,
                "team": team_name,
                "player_out": player,
                "player_in": assist,
            })

    halftime = score.get("halftime", {})
    fulltime = score.get("fulltime", {})

    return {
        "match_id": fix.get("id"),
        "status": _status_map(fix.get("status", {}).get("short", "")),
        "stage": league.get("round", ""),
        "utc_date": fix.get("date"),
        "home_team": home_name,
        "away_team": away_name,
        "home_score_ft": fulltime.get("home"),
        "away_score_ft": fulltime.get("away"),
        "home_score_ht": halftime.get("home"),
        "away_score_ht": halftime.get("away"),
        "winner": (
            "HOME_TEAM" if (fulltime.get("home") or 0) > (fulltime.get("away") or 0)
            else "AWAY_TEAM" if (fulltime.get("away") or 0) > (fulltime.get("home") or 0)
            else "DRAW" if fulltime.get("home") is not None
            else None
        ),
        "goals": goals,
        "cards": cards,
        "substitutions": subs,
        "last_updated": fix.get("timestamp"),
    }
