"""
Advanced stats fetcher for World Cup 2026.

Sources:
  1. API-Football /fixtures/statistics  — possession, shots, passes, fouls, corners
  2. FBref.com (scraping)               — xG, xGA, progressive passes/carries, PPDA, pressures
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"
FBREF_BASE = "https://fbref.com"

WC_LEAGUE_ID = 1
WC_SEASON = 2026


# ─── API-Football match statistics ────────────────────────────────────────────

def _api_headers():
    return {"x-apisports-key": API_FOOTBALL_KEY}


def get_fixture_statistics(fixture_id: int) -> dict:
    """
    Returns per-team stats for a fixture:
    possession, shots (total/on target), passes (total/accurate),
    fouls, corners, offsides, yellow/red cards, saves.
    """
    r = requests.get(
        f"{BASE_URL}/fixtures/statistics",
        headers=_api_headers(),
        params={"fixture": fixture_id},
        timeout=15,
    )
    r.raise_for_status()
    teams = {}
    for team_data in r.json().get("response", []):
        name = team_data["team"]["name"]
        stats = {s["type"]: s["value"] for s in team_data["statistics"]}
        teams[name] = _normalize_fixture_stats(stats)
    return teams  # {team_name: {stat: value}}


def _normalize_fixture_stats(raw: dict) -> dict:
    def _pct(val):
        if val is None:
            return None
        if isinstance(val, str) and "%" in val:
            return float(val.replace("%", "")) / 100
        return float(val) / 100 if float(val) > 1 else float(val)

    def _int(val):
        try:
            return int(val) if val is not None else 0
        except (ValueError, TypeError):
            return 0

    return {
        "possession": _pct(raw.get("Ball Possession")),
        "shots_total": _int(raw.get("Total Shots")),
        "shots_on_target": _int(raw.get("Shots on Goal")),
        "shots_off_target": _int(raw.get("Shots off Goal")),
        "shots_blocked": _int(raw.get("Blocked Shots")),
        "shots_inside_box": _int(raw.get("Shots insidebox")),
        "shots_outside_box": _int(raw.get("Shots outsidebox")),
        "passes_total": _int(raw.get("Total passes")),
        "passes_accurate": _int(raw.get("Passes accurate")),
        "pass_accuracy": _pct(raw.get("Passes %")),
        "fouls": _int(raw.get("Fouls")),
        "corners": _int(raw.get("Corner Kicks")),
        "offsides": _int(raw.get("Offsides")),
        "yellow_cards": _int(raw.get("Yellow Cards")),
        "red_cards": _int(raw.get("Red Cards")),
        "saves": _int(raw.get("Goalkeeper Saves")),
    }


def get_player_statistics(fixture_id: int) -> list[dict]:
    """
    Returns per-player stats for a fixture including:
    shots, passes, dribbles, tackles, duels, fouls, cards.
    """
    r = requests.get(
        f"{BASE_URL}/fixtures/players",
        headers=_api_headers(),
        params={"fixture": fixture_id},
        timeout=15,
    )
    r.raise_for_status()
    players = []
    for team_data in r.json().get("response", []):
        team_name = team_data["team"]["name"]
        for p in team_data.get("players", []):
            info = p.get("player", {})
            stats = p.get("statistics", [{}])[0]
            players.append({
                "name": info.get("name"),
                "team": team_name,
                "position": stats.get("games", {}).get("position"),
                "minutes": stats.get("games", {}).get("minutes", 0),
                "rating": stats.get("games", {}).get("rating"),
                "goals": stats.get("goals", {}).get("total", 0) or 0,
                "assists": stats.get("goals", {}).get("assists", 0) or 0,
                "shots_total": stats.get("shots", {}).get("total", 0) or 0,
                "shots_on": stats.get("shots", {}).get("on", 0) or 0,
                "passes_total": stats.get("passes", {}).get("total", 0) or 0,
                "key_passes": stats.get("passes", {}).get("key", 0) or 0,
                "pass_accuracy": stats.get("passes", {}).get("accuracy"),
                "dribbles_attempted": stats.get("dribbles", {}).get("attempts", 0) or 0,
                "dribbles_success": stats.get("dribbles", {}).get("success", 0) or 0,
                "tackles": stats.get("tackles", {}).get("total", 0) or 0,
                "interceptions": stats.get("tackles", {}).get("interceptions", 0) or 0,
                "duels_total": stats.get("duels", {}).get("total", 0) or 0,
                "duels_won": stats.get("duels", {}).get("won", 0) or 0,
                "aerial_duels_won": stats.get("duels", {}).get("aerial_won", 0) or 0,
                "fouls_drawn": stats.get("fouls", {}).get("drawn", 0) or 0,
                "fouls_committed": stats.get("fouls", {}).get("committed", 0) or 0,
                "yellow_cards": stats.get("cards", {}).get("yellow", 0) or 0,
                "red_cards": stats.get("cards", {}).get("red", 0) or 0,
            })
    return players


# ─── FBref scraping for xG, PPDA, progressive stats ──────────────────────────

def _fbref_headers():
    return {
        "User-Agent": "Mozilla/5.0 (compatible; worldcup-sentiment-tracker/1.0)",
        "Accept-Language": "en-US,en;q=0.9",
    }


def get_fbref_team_stats() -> dict:
    """
    Scrape team-level advanced stats from FBref World Cup 2026 page.
    Returns dict keyed by team name with xG, xGA, possession, PPDA etc.
    """
    try:
        import pandas as pd
    except ImportError:
        return {}

    url = f"{FBREF_BASE}/en/comps/1/World-Cup-Stats"
    try:
        r = requests.get(url, headers=_fbref_headers(), timeout=20)
        r.raise_for_status()
        tables = pd.read_html(r.text)
    except Exception:
        return {}

    stats = {}

    # Table 0 is usually the squad standard stats
    for table in tables:
        cols = [str(c).lower() for c in table.columns]
        # Look for xG column
        if not any("xg" in c for c in cols):
            continue
        for _, row in table.iterrows():
            team = str(row.iloc[0]).strip()
            if not team or team == "nan":
                continue
            entry = {}
            for col, val in zip(table.columns, row):
                col_str = str(col).lower()
                try:
                    fval = float(val)
                except (ValueError, TypeError):
                    continue
                if "xg" in col_str and "xga" not in col_str:
                    entry["xg"] = fval
                elif "xga" in col_str or ("xg" in col_str and "against" in col_str):
                    entry["xga"] = fval
                elif "poss" in col_str:
                    entry["possession_pct"] = fval / 100 if fval > 1 else fval
                elif "prog" in col_str and "pass" in col_str:
                    entry["progressive_passes"] = fval
                elif "prog" in col_str and ("carry" in col_str or "carr" in col_str):
                    entry["progressive_carries"] = fval
                elif "press" in col_str and "succ" not in col_str:
                    entry["pressures"] = fval
                elif "ppda" in col_str:
                    entry["ppda"] = fval
            if entry:
                stats[team] = entry

    return stats


def get_fbref_player_stats() -> dict:
    """
    Scrape player-level advanced stats from FBref.
    Returns dict keyed by player name.
    """
    try:
        import pandas as pd
    except ImportError:
        return {}

    url = f"{FBREF_BASE}/en/comps/1/stats/World-Cup-Stats"
    try:
        r = requests.get(url, headers=_fbref_headers(), timeout=20)
        r.raise_for_status()
        tables = pd.read_html(r.text)
    except Exception:
        return {}

    players = {}
    for table in tables:
        cols = [str(c).lower() for c in table.columns]
        if not any("xg" in c for c in cols):
            continue
        for _, row in table.iterrows():
            name = str(row.iloc[0]).strip()
            if not name or name == "nan" or name == "Player":
                continue
            entry = {}
            for col, val in zip(table.columns, row):
                col_str = str(col).lower()
                try:
                    fval = float(val)
                except (ValueError, TypeError):
                    continue
                if col_str in ("xg", "expected_xg") or (col_str == "xg" and "xga" not in col_str):
                    entry["xg"] = fval
                elif "xa" in col_str or "expected_xa" in col_str:
                    entry["xa"] = fval
                elif "npxg" in col_str:
                    entry["npxg"] = fval
                elif "key" in col_str and "pass" in col_str:
                    entry["key_passes"] = fval
                elif "prog" in col_str and "pass" in col_str:
                    entry["progressive_passes"] = fval
                elif "prog" in col_str and "carry" in col_str:
                    entry["progressive_carries"] = fval
                elif "press" in col_str:
                    entry["pressures"] = fval
                elif "aeriel" in col_str or "aerial" in col_str:
                    entry["aerial_won_pct"] = fval
            if entry:
                players[name] = entry

    return players


# ─── Aggregation helpers ───────────────────────────────────────────────────────

def compute_team_advanced_stats(fixture_stats: list[dict]) -> dict:
    """
    Aggregate per-fixture team stats into season averages.
    fixture_stats: list of dicts from get_fixture_statistics()
    Returns {team: {avg_possession, avg_shots_on_target, shot_accuracy,
                    avg_passes, avg_pass_accuracy, avg_corners,
                    avg_saves, shots_on_per_shot, ...}}
    """
    from collections import defaultdict
    team_data = defaultdict(list)
    for match_stats in fixture_stats:
        for team, stats in match_stats.items():
            team_data[team].append(stats)

    result = {}
    for team, games in team_data.items():
        n = len(games)
        def avg(key):
            vals = [g[key] for g in games if g.get(key) is not None]
            return sum(vals) / len(vals) if vals else 0

        shots_total = avg("shots_total")
        shots_on = avg("shots_on_target")
        result[team] = {
            "avg_possession": avg("possession"),
            "avg_shots_total": shots_total,
            "avg_shots_on_target": shots_on,
            "shot_accuracy": shots_on / shots_total if shots_total else 0,
            "avg_shots_inside_box": avg("shots_inside_box"),
            "avg_passes": avg("passes_total"),
            "avg_pass_accuracy": avg("pass_accuracy"),
            "avg_key_passes": avg("key_passes") if "key_passes" in games[0] else 0,
            "avg_corners": avg("corners"),
            "avg_fouls": avg("fouls"),
            "avg_saves": avg("saves"),
            "matches_sampled": n,
        }
    return result


def compute_player_advanced_stats(all_player_stats: list[list[dict]]) -> dict:
    """
    Aggregate per-fixture player stats into per-player totals/averages.
    Returns {player_name: {xg_per90, xa_per90, key_passes_per90,
                           dribble_success_rate, aerial_won_pct,
                           defensive_actions_per90, ...}}
    """
    from collections import defaultdict
    players = defaultdict(lambda: {
        "minutes": 0, "goals": 0, "assists": 0,
        "shots_total": 0, "shots_on": 0, "key_passes": 0,
        "dribbles_attempted": 0, "dribbles_success": 0,
        "tackles": 0, "interceptions": 0,
        "duels_total": 0, "duels_won": 0,
        "aerial_duels_won": 0, "fouls_drawn": 0,
        "yellow_cards": 0, "red_cards": 0,
        "team": None, "position": None, "appearances": 0,
    })

    for match_players in all_player_stats:
        for p in match_players:
            name = p.get("name")
            if not name:
                continue
            d = players[name]
            d["minutes"] += p.get("minutes") or 0
            d["goals"] += p.get("goals") or 0
            d["assists"] += p.get("assists") or 0
            d["shots_total"] += p.get("shots_total") or 0
            d["shots_on"] += p.get("shots_on") or 0
            d["key_passes"] += p.get("key_passes") or 0
            d["dribbles_attempted"] += p.get("dribbles_attempted") or 0
            d["dribbles_success"] += p.get("dribbles_success") or 0
            d["tackles"] += p.get("tackles") or 0
            d["interceptions"] += p.get("interceptions") or 0
            d["duels_total"] += p.get("duels_total") or 0
            d["duels_won"] += p.get("duels_won") or 0
            d["aerial_duels_won"] += p.get("aerial_duels_won") or 0
            d["fouls_drawn"] += p.get("fouls_drawn") or 0
            d["yellow_cards"] += p.get("yellow_cards") or 0
            d["red_cards"] += p.get("red_cards") or 0
            d["team"] = p.get("team") or d["team"]
            d["position"] = p.get("position") or d["position"]
            d["appearances"] += 1

    result = {}
    for name, d in players.items():
        p90 = d["minutes"] / 90 if d["minutes"] else 1
        da = d["dribbles_attempted"]
        result[name] = {
            "team": d["team"],
            "position": d["position"],
            "minutes": d["minutes"],
            "appearances": d["appearances"],
            "goals": d["goals"],
            "assists": d["assists"],
            "goals_per90": round(d["goals"] / p90, 3),
            "assists_per90": round(d["assists"] / p90, 3),
            "shots_per90": round(d["shots_total"] / p90, 3),
            "shots_on_per90": round(d["shots_on"] / p90, 3),
            "key_passes_per90": round(d["key_passes"] / p90, 3),
            "dribble_success_rate": round(d["dribbles_success"] / da, 3) if da else 0,
            "tackles_per90": round(d["tackles"] / p90, 3),
            "interceptions_per90": round(d["interceptions"] / p90, 3),
            "defensive_actions_per90": round((d["tackles"] + d["interceptions"]) / p90, 3),
            "duel_win_rate": round(d["duels_won"] / d["duels_total"], 3) if d["duels_total"] else 0,
            "aerial_won": d["aerial_duels_won"],
            "fouls_drawn_per90": round(d["fouls_drawn"] / p90, 3),
            "yellow_cards": d["yellow_cards"],
            "red_cards": d["red_cards"],
        }
    return result
