"""
Win probability model for World Cup 2026.

Trains a logistic regression on finished match features and predicts
win/draw/loss probabilities for upcoming matches.

Usage:
    python win_probability.py                  # show upcoming match predictions
    python win_probability.py --leaderboard    # show all team win probabilities
    python win_probability.py --players        # show top player stats
"""
import argparse
import os

import numpy as np
from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from features import (
    compute_player_features,
    compute_team_features,
    compute_team_sentiment,
    get_finished_matches,
    get_scheduled_matches,
    get_top_players_for_team,
)

load_dotenv()

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")


# ─── Feature vector ────────────────────────────────────────────────────────────

FEATURE_NAMES = [
    "home_form",
    "away_form",
    "home_avg_goals_scored",
    "away_avg_goals_scored",
    "home_avg_goals_conceded",
    "away_avg_goals_conceded",
    "home_goal_diff",
    "away_goal_diff",
    "home_clean_sheet_rate",
    "away_clean_sheet_rate",
    "home_comeback_wins",
    "away_comeback_wins",
    "home_sentiment",
    "away_sentiment",
    "form_delta",         # home_form - away_form
    "goal_diff_delta",    # home_goal_diff - away_goal_diff
    "sentiment_delta",    # home_sentiment - away_sentiment
]


def build_feature_vector(home: str, away: str, team_features: dict, team_sentiment: dict) -> np.ndarray:
    hf = team_features.get(home, {})
    af = team_features.get(away, {})
    hs = team_sentiment.get(home, {})
    as_ = team_sentiment.get(away, {})

    home_form = hf.get("form_score", 1.0)
    away_form = af.get("form_score", 1.0)
    home_gd = hf.get("goal_difference", 0)
    away_gd = af.get("goal_difference", 0)
    home_sent = hs.get("avg_sentiment", 0.5)
    away_sent = as_.get("avg_sentiment", 0.5)

    return np.array([
        home_form,
        away_form,
        hf.get("avg_goals_scored", 1.0),
        af.get("avg_goals_scored", 1.0),
        hf.get("avg_goals_conceded", 1.0),
        af.get("avg_goals_conceded", 1.0),
        home_gd,
        away_gd,
        hf.get("clean_sheet_rate", 0.0),
        af.get("clean_sheet_rate", 0.0),
        hf.get("comeback_wins", 0),
        af.get("comeback_wins", 0),
        home_sent,
        away_sent,
        home_form - away_form,
        home_gd - away_gd,
        home_sent - away_sent,
    ])


def encode_outcome(match: dict) -> int | None:
    """0=away win, 1=draw, 2=home win"""
    winner = match.get("winner")
    if winner == "HOME_TEAM":
        return 2
    elif winner == "DRAW":
        return 1
    elif winner == "AWAY_TEAM":
        return 0
    return None


# ─── Model ─────────────────────────────────────────────────────────────────────

def train_model(finished: list[dict], team_features: dict, team_sentiment: dict):
    X, y = [], []
    for m in finished:
        home = m.get("home_team")
        away = m.get("away_team")
        label = encode_outcome(m)
        if not home or not away or label is None:
            continue
        vec = build_feature_vector(home, away, team_features, team_sentiment)
        X.append(vec)
        y.append(label)

    if len(X) < 5:
        return None, None

    X = np.array(X)
    y = np.array(y)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        max_iter=500,
        C=1.0,
        random_state=42,
    )
    model.fit(X_scaled, y)
    return model, scaler


def predict_match(model, scaler, home: str, away: str, team_features: dict, team_sentiment: dict) -> dict:
    vec = build_feature_vector(home, away, team_features, team_sentiment).reshape(1, -1)
    vec_scaled = scaler.transform(vec)
    probs = model.predict_proba(vec_scaled)[0]

    # classes are ordered by encode_outcome: 0=away, 1=draw, 2=home
    class_order = model.classes_
    prob_map = dict(zip(class_order, probs))

    return {
        "home_win": round(prob_map.get(2, 0) * 100, 1),
        "draw": round(prob_map.get(1, 0) * 100, 1),
        "away_win": round(prob_map.get(0, 0) * 100, 1),
    }


# ─── Tournament leaderboard ────────────────────────────────────────────────────

def compute_tournament_win_probs(
    model, scaler, all_teams: list[str], team_features: dict, team_sentiment: dict
) -> dict[str, float]:
    """
    Estimate each team's probability of winning the tournament
    by simulating all remaining matchups using pairwise win probs.
    Uses a simplified Elo-style accumulation.
    """
    scores = {}
    for team in all_teams:
        total = 0.0
        opponents = [t for t in all_teams if t != team]
        for opp in opponents:
            pred = predict_match(model, scaler, team, opp, team_features, team_sentiment)
            total += pred["home_win"] / 100
        scores[team] = total / len(opponents) if opponents else 0.0

    # Normalize to probabilities
    total = sum(scores.values())
    return {t: round(s / total * 100, 1) for t, s in sorted(scores.items(), key=lambda x: -x[1])}


# ─── Display helpers ───────────────────────────────────────────────────────────

def print_match_prediction(home: str, away: str, pred: dict, team_features: dict,
                            player_features: dict, team_sentiment: dict):
    hf = team_features.get(home, {})
    af = team_features.get(away, {})
    hs = team_sentiment.get(home, {})
    as_ = team_sentiment.get(away, {})

    print(f"\n  {'─'*54}")
    print(f"  {home}  vs  {away}")
    print(f"  {'─'*54}")
    print(f"  Win probability:  {home} {pred['home_win']}%  |  Draw {pred['draw']}%  |  {away} {pred['away_win']}%")
    print()
    print(f"  {'Metric':<28} {'Home':>10} {'Away':>10}")
    print(f"  {'-'*50}")
    rows = [
        ("Form score (weighted)", "form_score"),
        ("Avg goals scored", "avg_goals_scored"),
        ("Avg goals conceded", "avg_goals_conceded"),
        ("Goal difference", "goal_difference"),
        ("Clean sheet rate", "clean_sheet_rate"),
        ("Comeback wins", "comeback_wins"),
    ]
    for label, key in rows:
        hv = hf.get(key, 0)
        av = af.get(key, 0)
        hv_str = f"{hv:.2f}" if isinstance(hv, float) else str(hv)
        av_str = f"{av:.2f}" if isinstance(av, float) else str(av)
        print(f"  {label:<28} {hv_str:>10} {av_str:>10}")

    print(f"  {'Pre-match sentiment':<28} {hs.get('avg_sentiment', 0.5):>10.3f} {as_.get('avg_sentiment', 0.5):>10.3f}")
    print(f"  {'Positive ratio':<28} {hs.get('positive_ratio', 0):>10.1%} {as_.get('positive_ratio', 0):>10.1%}")

    # Key players
    for team in [home, away]:
        top = get_top_players_for_team(player_features, team, n=3)
        if top:
            print(f"\n  Key players — {team}:")
            for p in top:
                print(f"    {p['name']:<25} {p['goals']}G {p['assists']}A  ({p['goal_contributions_per_match']:.2f} contrib/match)")


def print_leaderboard(win_probs: dict, team_features: dict):
    print(f"\n  {'Rank':<5} {'Team':<30} {'Tournament Win %':>16} {'Form':>8} {'GD':>6} {'Pts':>6}")
    print(f"  {'─'*72}")
    for rank, (team, prob) in enumerate(win_probs.items(), 1):
        tf = team_features.get(team, {})
        form = tf.get("form_score", 0)
        gd = tf.get("goal_difference", 0)
        pts = tf.get("total_points", 0)
        bar = "█" * int(prob / 2)
        print(f"  {rank:<5} {team:<30} {prob:>14.1f}%  {form:>7.2f} {gd:>6} {pts:>6.0f}  {bar}")


def print_player_stats(player_features: dict):
    players = sorted(
        player_features.values(),
        key=lambda p: p.get("goal_contributions_per_match", 0),
        reverse=True,
    )
    print(f"\n  {'Player':<25} {'Team':<25} {'G':>4} {'A':>4} {'G+A/M':>8} {'YC':>4} {'RC':>4}")
    print(f"  {'─'*76}")
    for p in players[:20]:
        name = list(player_features.keys())[list(player_features.values()).index(p)]
        print(
            f"  {name:<25} {str(p.get('team','')):<25} "
            f"{p['goals']:>4} {p['assists']:>4} "
            f"{p['goal_contributions_per_match']:>8.2f} "
            f"{p['yellow_cards']:>4} {p['red_cards']:>4}"
        )


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="World Cup win probability model")
    parser.add_argument("--leaderboard", action="store_true", help="Show tournament win probability leaderboard")
    parser.add_argument("--players", action="store_true", help="Show top player stats")
    parser.add_argument("--hours-sentiment", type=int, default=48, help="Hours of sentiment history to use")
    args = parser.parse_args()

    es = Elasticsearch(ES_HOST)

    print("Loading match data from Elasticsearch...")
    finished = get_finished_matches(es)
    scheduled = get_scheduled_matches(es)

    if not finished:
        print("[WARN] No finished matches in ES. Run producer_football.py first to sync match data.")
        return

    print(f"  {len(finished)} finished matches, {len(scheduled)} upcoming")

    print("Computing team features...")
    team_features = compute_team_features(finished)

    print("Computing player features...")
    player_features = compute_player_features(finished)

    all_teams = list(team_features.keys())
    print(f"  {len(all_teams)} teams, {len(player_features)} players tracked")

    print(f"Fetching {args.hours_sentiment}h sentiment signals...")
    team_sentiment = {}
    for team in all_teams:
        team_sentiment[team] = compute_team_sentiment(es, team, hours_back=args.hours_sentiment)

    print("Training logistic regression model...")
    model, scaler = train_model(finished, team_features, team_sentiment)

    if model is None:
        print("[WARN] Not enough finished matches to train model (need at least 5).")
        return

    print(f"  Trained on {len(finished)} matches with {len(FEATURE_NAMES)} features\n")

    # ── Player stats ──
    if args.players:
        print("=" * 78)
        print("  TOP PLAYERS BY GOAL CONTRIBUTIONS PER MATCH")
        print("=" * 78)
        print_player_stats(player_features)
        return

    # ── Tournament leaderboard ──
    if args.leaderboard:
        print("=" * 78)
        print("  TOURNAMENT WIN PROBABILITY LEADERBOARD")
        print("=" * 78)
        win_probs = compute_tournament_win_probs(model, scaler, all_teams, team_features, team_sentiment)
        print_leaderboard(win_probs, team_features)
        return

    # ── Upcoming match predictions ──
    if not scheduled:
        print("No upcoming matches found. Showing finished match retrospective instead.\n")
        to_predict = finished[-5:]
    else:
        to_predict = scheduled[:10]

    print("=" * 78)
    print("  UPCOMING MATCH WIN PROBABILITIES")
    print("=" * 78)

    for m in to_predict:
        home = m.get("home_team")
        away = m.get("away_team")
        date = m.get("utc_date", "")[:10]
        if not home or not away:
            continue
        print(f"\n  {date}")
        pred = predict_match(model, scaler, home, away, team_features, team_sentiment)
        print_match_prediction(home, away, pred, team_features, player_features, team_sentiment)

    print(f"\n{'─'*78}")
    print("  Run with --leaderboard to see full tournament win probability rankings.")
    print("  Run with --players to see top player stats.")


if __name__ == "__main__":
    main()
