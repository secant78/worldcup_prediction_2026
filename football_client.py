import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.football-data.org/v4"
# World Cup 2026 competition code
WC_CODE = "WC"


def _headers():
    api_key = os.getenv("FOOTBALL_DATA_API_KEY", "")
    return {"X-Auth-Token": api_key}


def get_competition():
    """Get World Cup competition info and current season."""
    r = requests.get(f"{BASE_URL}/competitions/{WC_CODE}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def get_matches(status: str = None) -> list[dict]:
    """
    Fetch World Cup matches.
    status: SCHEDULED | LIVE | IN_PLAY | PAUSED | FINISHED | POSTPONED
    """
    params = {}
    if status:
        params["status"] = status
    r = requests.get(f"{BASE_URL}/competitions/{WC_CODE}/matches", headers=_headers(), params=params, timeout=10)
    r.raise_for_status()
    return r.json().get("matches", [])


def get_match(match_id: int) -> dict:
    """Get full details for a single match including goals, cards, substitutions."""
    r = requests.get(f"{BASE_URL}/matches/{match_id}", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json()


def get_live_matches() -> list[dict]:
    """Return matches currently in play."""
    return get_matches(status="LIVE") + get_matches(status="IN_PLAY") + get_matches(status="PAUSED")


def get_standings() -> list[dict]:
    """Get group stage standings."""
    r = requests.get(f"{BASE_URL}/competitions/{WC_CODE}/standings", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json().get("standings", [])


def get_top_scorers() -> list[dict]:
    """Get top scorers for the tournament."""
    r = requests.get(f"{BASE_URL}/competitions/{WC_CODE}/scorers", headers=_headers(), timeout=10)
    r.raise_for_status()
    return r.json().get("scorers", [])


def normalize_match_event(match: dict) -> dict:
    """Flatten a match object into a clean document for Kafka/ES."""
    home = match.get("homeTeam", {})
    away = match.get("awayTeam", {})
    score = match.get("score", {})
    full = score.get("fullTime", {})
    half = score.get("halfTime", {})

    goals = []
    cards = []
    subs = []
    for ref in match.get("referees", []):
        pass  # referees tracked separately

    # Goals and events come from the goals array in full match detail
    for goal in match.get("goals", []):
        goals.append({
            "minute": goal.get("minute"),
            "team": goal.get("team", {}).get("name"),
            "scorer": goal.get("scorer", {}).get("name"),
            "assist": (goal.get("assist") or {}).get("name"),
            "type": goal.get("type"),
        })

    for booking in match.get("bookings", []):
        cards.append({
            "minute": booking.get("minute"),
            "team": booking.get("team", {}).get("name"),
            "player": booking.get("player", {}).get("name"),
            "card": booking.get("card"),
        })

    for sub in match.get("substitutions", []):
        subs.append({
            "minute": sub.get("minute"),
            "team": sub.get("team", {}).get("name"),
            "player_out": sub.get("playerOut", {}).get("name"),
            "player_in": sub.get("playerIn", {}).get("name"),
        })

    return {
        "match_id": match.get("id"),
        "status": match.get("status"),
        "matchday": match.get("matchday"),
        "stage": match.get("stage"),
        "utc_date": match.get("utcDate"),
        "home_team": home.get("name"),
        "away_team": away.get("name"),
        "home_score_ft": full.get("home"),
        "away_score_ft": full.get("away"),
        "home_score_ht": half.get("home"),
        "away_score_ht": half.get("away"),
        "winner": score.get("winner"),
        "goals": goals,
        "cards": cards,
        "substitutions": subs,
        "last_updated": match.get("lastUpdated"),
    }
