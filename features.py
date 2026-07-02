"""
Feature engineering for the win probability model.

Pulls finished match data from Elasticsearch and sentiment data
to compute per-team and per-player signals.
"""
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import numpy as np
from dotenv import load_dotenv
from elasticsearch import Elasticsearch

load_dotenv()

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "worldcup-sentiment")
ES_MATCHES_INDEX = os.getenv("ES_MATCHES_INDEX", "worldcup-matches")

# How much to decay older match weights (most recent match = 1.0)
RECENCY_DECAY = 0.8


# ─── Match data helpers ────────────────────────────────────────────────────────

def get_finished_matches(es) -> list[dict]:
    query = {
        "size": 200,
        "query": {"term": {"status": "FINISHED"}},
        "sort": [{"utc_date": {"order": "asc"}}],
    }
    res = es.search(index=ES_MATCHES_INDEX, body=query)
    return [h["_source"] for h in res["hits"]["hits"]]


def get_scheduled_matches(es) -> list[dict]:
    query = {
        "size": 200,
        "query": {"terms": {"status": ["SCHEDULED", "TIMED"]}},
        "sort": [{"utc_date": {"order": "asc"}}],
    }
    res = es.search(index=ES_MATCHES_INDEX, body=query)
    return [h["_source"] for h in res["hits"]["hits"]]


# ─── Team feature computation ──────────────────────────────────────────────────

def compute_team_features(matches: list[dict]) -> dict[str, dict]:
    """
    For each team compute form signals from their finished matches.
    Returns dict keyed by team name.
    """
    # Group matches per team
    team_matches: dict[str, list[dict]] = defaultdict(list)
    for m in matches:
        home = m.get("home_team")
        away = m.get("away_team")
        if home:
            team_matches[home].append({"match": m, "side": "home"})
        if away:
            team_matches[away].append({"match": m, "side": "away"})

    features = {}
    for team, team_match_list in team_matches.items():
        # Sort by date ascending so recency weights are applied correctly
        team_match_list.sort(key=lambda x: x["match"].get("utc_date", ""))
        n = len(team_match_list)

        points = []
        gf = []  # goals for
        ga = []  # goals against
        shots_on = []
        clean_sheets = []
        cards_per_match = []
        comeback_wins = 0

        for i, entry in enumerate(team_match_list):
            m = entry["match"]
            side = entry["side"]
            weight = RECENCY_DECAY ** (n - 1 - i)

            ht_for = (m.get("home_score_ht") if side == "home" else m.get("away_score_ht")) or 0
            ht_against = (m.get("away_score_ht") if side == "home" else m.get("home_score_ht")) or 0
            ft_for = (m.get("home_score_ft") if side == "home" else m.get("away_score_ft")) or 0
            ft_against = (m.get("away_score_ft") if side == "home" else m.get("home_score_ft")) or 0

            winner = m.get("winner")
            if winner == "HOME_TEAM" and side == "home":
                pts = 3
            elif winner == "AWAY_TEAM" and side == "away":
                pts = 3
            elif winner == "DRAW":
                pts = 1
            else:
                pts = 0

            points.append(pts * weight)
            gf.append(ft_for * weight)
            ga.append(ft_against * weight)
            clean_sheets.append(1 if ft_against == 0 else 0)

            # Comeback win: losing at HT but winning FT
            if ht_for < ht_against and ft_for > ft_against:
                comeback_wins += 1

            # Cards
            team_cards = sum(
                1 for c in m.get("cards", [])
                if c.get("team") == team
            )
            cards_per_match.append(team_cards)

            # Goals scored by team players (for player contribution)
            team_goals = [
                g for g in m.get("goals", [])
                if g.get("team") == team
            ]
            shots_on.append(len(team_goals))  # proxy: goals ≈ shots on target that converted

        total_weight = sum(RECENCY_DECAY ** i for i in range(n))

        features[team] = {
            "matches_played": n,
            "form_score": sum(points) / total_weight if total_weight else 0,
            "avg_goals_scored": sum(gf) / total_weight if total_weight else 0,
            "avg_goals_conceded": sum(ga) / total_weight if total_weight else 0,
            "goal_difference": sum(gf) - sum(ga),
            "clean_sheet_rate": np.mean(clean_sheets) if clean_sheets else 0,
            "comeback_wins": comeback_wins,
            "avg_cards": np.mean(cards_per_match) if cards_per_match else 0,
            "total_points": sum(p / (RECENCY_DECAY ** (n - 1 - i)) for i, p in enumerate(points)),
        }

    return features


# ─── Player feature computation ────────────────────────────────────────────────

def compute_player_features(matches: list[dict]) -> dict[str, dict]:
    """
    Compute per-player stats across all finished matches.
    Returns dict keyed by player name.
    """
    player_stats: dict[str, dict] = defaultdict(lambda: {
        "goals": 0,
        "assists": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "matches": 0,
        "team": None,
    })

    seen_matches_per_player: dict[str, set] = defaultdict(set)

    for m in matches:
        match_id = m.get("match_id")

        for goal in m.get("goals", []):
            scorer = goal.get("scorer")
            assist = goal.get("assist")
            team = goal.get("team")
            if scorer:
                player_stats[scorer]["goals"] += 1
                player_stats[scorer]["team"] = team
                seen_matches_per_player[scorer].add(match_id)
            if assist:
                player_stats[assist]["assists"] += 1
                player_stats[assist]["team"] = team
                seen_matches_per_player[assist].add(match_id)

        for card in m.get("cards", []):
            player = card.get("player")
            team = card.get("team")
            card_type = card.get("card", "")
            if player:
                if "YELLOW" in card_type.upper():
                    player_stats[player]["yellow_cards"] += 1
                elif "RED" in card_type.upper():
                    player_stats[player]["red_cards"] += 1
                player_stats[player]["team"] = team
                seen_matches_per_player[player].add(match_id)

    for player, stats in player_stats.items():
        stats["matches"] = len(seen_matches_per_player[player])
        stats["goals_per_match"] = stats["goals"] / stats["matches"] if stats["matches"] else 0
        stats["goal_contributions_per_match"] = (
            (stats["goals"] + stats["assists"]) / stats["matches"]
            if stats["matches"] else 0
        )

    return dict(player_stats)


# ─── Sentiment features ────────────────────────────────────────────────────────

def compute_team_sentiment(es, team_name: str, hours_back: int = 48) -> dict:
    """
    Compute average sentiment for a team in the N hours before now.
    Used as pre-match sentiment signal.
    """
    query = {
        "size": 0,
        "query": {
            "bool": {
                "must": [
                    {"match": {"text": team_name}},
                    {"range": {"timestamp": {"gte": f"now-{hours_back}h"}}},
                ]
            }
        },
        "aggs": {
            "avg_sentiment": {"avg": {"field": "sentiment_score"}},
            "sentiment_breakdown": {"terms": {"field": "sentiment_label"}},
            "post_count": {"value_count": {"field": "sentiment_label"}},
        },
    }
    res = es.search(index=ES_INDEX, body=query)
    aggs = res["aggregations"]
    breakdown = {
        b["key"]: b["doc_count"]
        for b in aggs["sentiment_breakdown"]["buckets"]
    }
    return {
        "avg_sentiment": aggs["avg_sentiment"]["value"] or 0.5,
        "post_count": aggs["post_count"]["value"],
        "positive_ratio": breakdown.get("positive", 0) / max(aggs["post_count"]["value"], 1),
        "negative_ratio": breakdown.get("negative", 0) / max(aggs["post_count"]["value"], 1),
    }


def get_top_players_for_team(player_features: dict, team: str, n: int = 3) -> list[dict]:
    """Return top N players for a team by goal contributions per match."""
    team_players = [
        {"name": name, **stats}
        for name, stats in player_features.items()
        if stats.get("team") == team
    ]
    return sorted(team_players, key=lambda p: p["goal_contributions_per_match"], reverse=True)[:n]
