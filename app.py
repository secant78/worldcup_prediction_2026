"""
World Cup 2026 — Sentiment & Predictions Dashboard
Run: streamlit run app.py
"""
import os
import time

import numpy as np
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from elasticsearch import Elasticsearch

from analysis import detect_spikes, get_match_events_near, get_sentiment_timeline
from features import (
    compute_player_features,
    compute_team_features,
    compute_team_sentiment,
    get_finished_matches,
    get_scheduled_matches,
    get_top_players_for_team,
    merge_advanced_stats,
)
from stats_client import (
    compute_player_advanced_stats,
    compute_team_advanced_stats,
    get_fbref_player_stats,
    get_fbref_team_stats,
)
from win_probability import (
    build_feature_vector,
    compute_tournament_win_probs,
    predict_match,
    train_model,
)

load_dotenv()

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "worldcup-sentiment")

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="World Cup 2026 · Sentiment & Predictions",
    page_icon="⚽",
    layout="wide",
)

st.markdown("""
<style>
    .metric-card {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 10px;
        padding: 1rem 1.2rem;
        text-align: center;
    }
    .metric-card .value { font-size: 2rem; font-weight: 700; color: #0f172a; }
    .metric-card .label { font-size: 0.8rem; color: #64748b; margin-top: 2px; }
    .win-bar-home { background: #0d9488; border-radius: 4px; height: 20px; }
    .win-bar-draw { background: #94a3b8; border-radius: 4px; height: 20px; }
    .win-bar-away { background: #e05694; border-radius: 4px; height: 20px; }
    .spike-positive { border-left: 4px solid #16a34a; padding-left: 0.8rem; }
    .spike-negative { border-left: 4px solid #dc2626; padding-left: 0.8rem; }
</style>
""", unsafe_allow_html=True)

# ─── Data loading ──────────────────────────────────────────────────────────────
@st.cache_resource
def get_es():
    return Elasticsearch(ES_HOST, verify_certs=False, ssl_show_warn=False)


@st.cache_data(ttl=300)
def load_all_data():
    es = get_es()
    finished = get_finished_matches(es)
    scheduled = get_scheduled_matches(es)
    team_features = compute_team_features(finished) if finished else {}
    player_features = compute_player_features(finished) if finished else {}
    all_teams = list(team_features.keys())
    team_sentiment = {t: compute_team_sentiment(es, t) for t in all_teams}

    # Merge FBref advanced stats (xG, PPDA, progressive passes etc.)
    fbref_team = get_fbref_team_stats()
    fbref_player = get_fbref_player_stats()
    if fbref_team:
        merge_advanced_stats(team_features, fbref_team)

    model, scaler = train_model(finished, team_features, team_sentiment) if len(finished) >= 5 else (None, None)
    return finished, scheduled, team_features, player_features, team_sentiment, model, scaler, all_teams, fbref_player


@st.cache_data(ttl=60)
def load_sentiment_timeline(hours_back, interval):
    es = get_es()
    result = get_sentiment_timeline(es, interval=interval, hours_back=hours_back)
    buckets = result["aggregations"]["over_time"]["buckets"]
    rows = []
    for b in buckets:
        avg = b["avg_score"]["value"]
        if avg is None:
            continue
        breakdown = {x["key"]: x["doc_count"] for x in b["sentiment_breakdown"]["buckets"]}
        entities = [e["key"] for e in b["top_entities"]["names"]["buckets"]]
        rows.append({
            "timestamp": pd.to_datetime(b["key_as_string"]),
            "avg_sentiment": avg,
            "post_count": b["doc_count"],
            "positive": breakdown.get("positive", 0),
            "negative": breakdown.get("negative", 0),
            "neutral": breakdown.get("neutral", 0),
            "top_entities": ", ".join(entities),
        })
    return pd.DataFrame(rows)


@st.cache_data(ttl=120)
def load_spikes(hours_back):
    es = get_es()
    return detect_spikes(es, hours_back=hours_back)


# ─── Header ───────────────────────────────────────────────────────────────────
st.title("⚽ World Cup 2026")
st.caption("Real-time sentiment analysis · Win probability model · Player & team stats")

try:
    finished, scheduled, team_features, player_features, team_sentiment, model, scaler, all_teams, fbref_player = load_all_data()
    es_ok = True
except Exception as e:
    st.error(f"Cannot connect to Elasticsearch at {ES_HOST}. Make sure `docker-compose up -d` is running.\n\n`{e}`")
    st.stop()

# ─── Top KPIs ─────────────────────────────────────────────────────────────────
k1, k2, k3, k4, k5 = st.columns(5)
total_posts = sum(t.get("post_count", 0) for t in team_sentiment.values())
avg_sent_all = np.mean([t.get("avg_sentiment", 0.5) for t in team_sentiment.values()]) if team_sentiment else 0.5

k1.metric("Matches Played", len(finished))
k2.metric("Matches Upcoming", len(scheduled))
k3.metric("Teams Tracked", len(all_teams))
k4.metric("Players Tracked", len(player_features))
k5.metric("Avg Sentiment", f"{avg_sent_all:.3f}")

st.divider()

# ─── Tabs ─────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "🎯 Match Predictions",
    "🏆 Tournament Leaderboard",
    "📊 Sentiment Analysis",
    "👤 Player Stats",
    "⚡ Sentiment Spikes",
    "📐 Advanced Stats",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — MATCH PREDICTIONS
# ══════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Match Win Probabilities")

    if model is None:
        st.warning("Not enough finished matches to train the model yet (need at least 5). Check back after more matches are played.")
    else:
        matches_to_show = scheduled[:10] if scheduled else finished[-5:]
        label = "Upcoming Matches" if scheduled else "Recent Matches (retrospective)"
        st.caption(label)

        for m in matches_to_show:
            home = m.get("home_team", "")
            away = m.get("away_team", "")
            date = (m.get("utc_date") or "")[:10]
            if not home or not away:
                continue

            pred = predict_match(model, scaler, home, away, team_features, team_sentiment)
            hf = team_features.get(home, {})
            af = team_features.get(away, {})
            hs = team_sentiment.get(home, {})
            as_ = team_sentiment.get(away, {})

            with st.expander(f"**{home}** vs **{away}** — {date}", expanded=True):
                # Win probability bar — single HTML block so widths are always proportional
                hw, dr, aw = pred["home_win"], pred["draw"], pred["away_win"]
                st.markdown(f"""
<div style='display:flex;width:100%;border-radius:8px;overflow:hidden;font-size:0.85rem;font-weight:700;color:white;'>
  <div style='background:#0d9488;width:{hw}%;padding:10px 6px;text-align:center;min-width:60px;'>
    {home}<br>{hw}%
  </div>
  <div style='background:#94a3b8;width:{dr}%;padding:10px 6px;text-align:center;min-width:50px;'>
    Draw<br>{dr}%
  </div>
  <div style='background:#e05694;width:{aw}%;padding:10px 6px;text-align:center;min-width:60px;'>
    {away}<br>{aw}%
  </div>
</div>
""", unsafe_allow_html=True)
                st.markdown("")

                # Stats comparison
                left, right = st.columns(2)
                metrics = [
                    ("Form score", "form_score", ".2f"),
                    ("Avg goals scored", "avg_goals_scored", ".2f"),
                    ("Avg goals conceded", "avg_goals_conceded", ".2f"),
                    ("Goal difference", "goal_difference", "d"),
                    ("Clean sheet rate", "clean_sheet_rate", ".0%"),
                    ("Comeback wins", "comeback_wins", "d"),
                ]
                with left:
                    st.markdown(f"**{home}**")
                    for label_, key, fmt in metrics:
                        val = hf.get(key, 0)
                        st.metric(label_, f"{int(val):{fmt}}" if fmt == "d" else f"{val:{fmt}}")
                    st.metric("Pre-match sentiment", f"{hs.get('avg_sentiment', 0.5):.3f}")

                with right:
                    st.markdown(f"**{away}**")
                    for label_, key, fmt in metrics:
                        val = af.get(key, 0)
                        st.metric(label_, f"{int(val):{fmt}}" if fmt == "d" else f"{val:{fmt}}")
                    st.metric("Pre-match sentiment", f"{as_.get('avg_sentiment', 0.5):.3f}")

                # Key players
                st.markdown("**Key Players**")
                pc1, pc2 = st.columns(2)
                for col, team in [(pc1, home), (pc2, away)]:
                    top = get_top_players_for_team(player_features, team, n=3)
                    with col:
                        st.markdown(f"*{team}*")
                        if top:
                            df = pd.DataFrame([{
                                "Player": p["name"],
                                "G": p["goals"],
                                "A": p["assists"],
                                "G+A/M": round(p["goal_contributions_per_match"], 2),
                            } for p in top])
                            st.dataframe(df, hide_index=True, use_container_width=True)
                        else:
                            st.caption("No player data yet.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TOURNAMENT LEADERBOARD
# ══════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Tournament Win Probability Leaderboard")

    if model is None:
        st.warning("Model not trained yet — need at least 5 finished matches.")
    elif not all_teams:
        st.warning("No team data available.")
    else:
        win_probs = compute_tournament_win_probs(model, scaler, all_teams, team_features, team_sentiment)

        rows = []
        for rank, (team, prob) in enumerate(win_probs.items(), 1):
            tf = team_features.get(team, {})
            ts = team_sentiment.get(team, {})
            rows.append({
                "Rank": rank,
                "Team": team,
                "Win %": prob,
                "Form": round(tf.get("form_score", 0), 2),
                "Goal Diff": tf.get("goal_difference", 0),
                "Points": int(tf.get("total_points", 0)),
                "Avg Sentiment": round(ts.get("avg_sentiment", 0.5), 3),
                "Matches": tf.get("matches_played", 0),
            })

        df = pd.DataFrame(rows)

        # Bar chart
        st.bar_chart(df.set_index("Team")["Win %"], color="#0d9488", height=300)

        # Table
        st.dataframe(
            df.style.format({"Win %": "{:.1f}%", "Avg Sentiment": "{:.3f}"}),
            use_container_width=True,
            hide_index=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SENTIMENT ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("Sentiment Over Time")

    col_h, col_i = st.columns([2, 2])
    hours_back = col_h.selectbox("Time window", [6, 12, 24, 48, 72], index=2, format_func=lambda x: f"Last {x}h")
    interval = col_i.selectbox("Bucket size", ["5m", "15m", "30m", "1h"], index=1)

    try:
        df_sent = load_sentiment_timeline(hours_back, interval)
    except Exception as e:
        st.warning(f"No sentiment data yet. Start the producers and consumer to ingest posts.\n\n`{e}`")
        df_sent = pd.DataFrame()

    if not df_sent.empty:
        # Sentiment line chart
        st.line_chart(df_sent.set_index("timestamp")["avg_sentiment"], color="#0d9488", height=220)

        # Volume bar chart
        st.caption("Post volume per bucket")
        st.bar_chart(df_sent.set_index("timestamp")["post_count"], color="#5b8def", height=150)

        # Breakdown stacked
        st.caption("Sentiment breakdown")
        st.area_chart(
            df_sent.set_index("timestamp")[["positive", "negative", "neutral"]],
            color=["#16a34a", "#dc2626", "#94a3b8"],
            height=180,
        )

        # Team sentiment comparison
        st.subheader("Team Sentiment (last 48h)")
        if team_sentiment:
            ts_rows = []
            for team, ts in team_sentiment.items():
                ts_rows.append({
                    "Team": team,
                    "Avg Sentiment": round(ts.get("avg_sentiment", 0.5), 3),
                    "Positive %": round(ts.get("positive_ratio", 0) * 100, 1),
                    "Negative %": round(ts.get("negative_ratio", 0) * 100, 1),
                    "Posts": ts.get("post_count", 0),
                })
            ts_df = pd.DataFrame(ts_rows).sort_values("Avg Sentiment", ascending=False)
            st.bar_chart(ts_df.set_index("Team")["Avg Sentiment"], color="#0d9488", height=250)
            st.dataframe(ts_df, hide_index=True, use_container_width=True)
    else:
        st.info("No sentiment data yet. Run `producer_reddit.py` and `consumer.py` to start ingesting posts.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — PLAYER STATS
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("Player Statistics")

    if not player_features:
        st.info("No player data yet. Run `producer_football.py` to sync match data.")
    else:
        player_rows = []
        for name, stats in player_features.items():
            player_rows.append({
                "Player": name,
                "Team": stats.get("team", ""),
                "Goals": stats["goals"],
                "Assists": stats["assists"],
                "G+A": stats["goals"] + stats["assists"],
                "G+A/Match": round(stats["goal_contributions_per_match"], 2),
                "Yellow Cards": stats["yellow_cards"],
                "Red Cards": stats["red_cards"],
                "Matches": stats["matches"],
            })

        df_players = pd.DataFrame(player_rows).sort_values("G+A/Match", ascending=False)

        # Filters
        f1, f2 = st.columns(2)
        teams = ["All"] + sorted(df_players["Team"].unique().tolist())
        selected_team = f1.selectbox("Filter by team", teams)
        min_matches = f2.slider("Min matches played", 1, max(df_players["Matches"].max(), 1), 1)

        filtered = df_players.copy()
        if selected_team != "All":
            filtered = filtered[filtered["Team"] == selected_team]
        filtered = filtered[filtered["Matches"] >= min_matches]

        # Top scorers chart
        top20 = filtered.head(20)
        if not top20.empty:
            st.caption("Top 20 by goal contributions per match")
            st.bar_chart(top20.set_index("Player")["G+A/Match"], color="#0d9488", height=280)

        st.dataframe(
            filtered,
            use_container_width=True,
            hide_index=True,
        )

        # Team scoring depth
        st.subheader("Team Scoring Depth")
        st.caption("Total goals + assists per team")
        team_ga = df_players.groupby("Team")[["Goals", "Assists"]].sum().sort_values("Goals", ascending=False)
        st.bar_chart(team_ga, color=["#0d9488", "#5b8def"], height=280)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — SENTIMENT SPIKES
# ══════════════════════════════════════════════════════════════════════════════
with tab5:
    st.subheader("Sentiment Spikes & Match Event Correlation")
    st.caption("Spikes are moments where sentiment deviated more than 2σ from the mean.")

    spike_hours = st.selectbox("Look back", [6, 12, 24, 48, 72], index=2, format_func=lambda x: f"Last {x}h", key="spike_hours")

    try:
        spikes = load_spikes(spike_hours)
    except Exception:
        spikes = []

    if not spikes:
        st.info("No significant spikes detected in this window. Try a longer time range or wait for more data.")
    else:
        spikes_sorted = sorted(spikes, key=lambda s: s["deviation_sigmas"], reverse=True)
        st.metric("Spikes detected", len(spikes_sorted))

        for spike in spikes_sorted:
            color = "#16a34a" if spike["direction"] == "positive" else "#dc2626"
            direction_emoji = "📈" if spike["direction"] == "positive" else "📉"

            with st.expander(f"{direction_emoji} {spike['timestamp']}  —  {spike['deviation_sigmas']}σ {spike['direction']} spike", expanded=False):
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Avg Sentiment", spike["avg_score"])
                m2.metric("Deviation", f"{spike['deviation_sigmas']}σ")
                m3.metric("Posts", spike["post_count"])
                m4.metric("Direction", spike["direction"].capitalize())

                # Breakdown
                bd = spike["breakdown"]
                if bd:
                    bd_df = pd.DataFrame([{"Label": k, "Count": v} for k, v in bd.items()])
                    st.bar_chart(bd_df.set_index("Label")["Count"], height=120)

                # Entities
                if spike["top_entities"]:
                    st.markdown(f"**Mentioned entities:** {', '.join(spike['top_entities'])}")

                # Match events near this spike
                try:
                    es = get_es()
                    events = get_match_events_near(es, spike["timestamp"])
                    if events:
                        st.markdown("**Match events near this spike:**")
                        for ev in events:
                            icon = "⚽" if ev["type"] == "GOAL" else "🟨" if "YELLOW" in ev["type"] else "🟥"
                            st.markdown(f"{icon} **{ev['type']}** — {ev['match']} — {ev['detail']}")
                    else:
                        st.caption("No match events found near this spike.")
                except Exception:
                    st.caption("Could not load match events.")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — ADVANCED STATS
# ══════════════════════════════════════════════════════════════════════════════
with tab6:
    st.subheader("Advanced Team Stats")
    st.caption("xG/xGA from FBref · Possession, shots, passes from API-Football")

    adv_rows = []
    for team in all_teams:
        tf = team_features.get(team, {})
        adv_rows.append({
            "Team": team,
            "xG": round(tf.get("xg", 0), 2),
            "xGA": round(tf.get("xga", 0), 2),
            "xG Diff": round(tf.get("xg_difference", 0), 2),
            "Possession %": round(tf.get("avg_possession", 0) * 100, 1),
            "Shot Acc %": round(tf.get("shot_accuracy", 0) * 100, 1),
            "Pass Acc %": round(tf.get("avg_pass_accuracy", 0) * 100, 1),
            "Shots on Target": round(tf.get("avg_shots_on_target", 0), 1),
            "Corners/Game": round(tf.get("avg_corners", 0), 1),
            "PPDA": round(tf.get("ppda", 0), 2),
            "Prog Passes": round(tf.get("progressive_passes", 0), 1),
            "Prog Carries": round(tf.get("progressive_carries", 0), 1),
            "Pressures": round(tf.get("pressures", 0), 1),
        })

    adv_df = pd.DataFrame(adv_rows).sort_values("xG Diff", ascending=False)

    has_xg = adv_df["xG"].sum() > 0

    if has_xg:
        # xG vs xGA scatter proxy — use bar chart
        st.markdown("**xG vs xGA (attacking vs defensive quality)**")
        xg_df = adv_df.set_index("Team")[["xG", "xGA"]].sort_values("xG", ascending=False)
        st.bar_chart(xg_df, color=["#0d9488", "#e05694"], height=300)

        st.markdown("**xG Difference (xG − xGA)**")
        st.bar_chart(adv_df.set_index("Team")["xG Diff"], color="#5b8def", height=250)

        st.markdown("**Possession %**")
        st.bar_chart(adv_df.set_index("Team")["Possession %"], color="#0d9488", height=250)

        st.markdown("**Pressing Intensity (PPDA — lower = more aggressive)**")
        ppda_df = adv_df[adv_df["PPDA"] > 0].set_index("Team")["PPDA"].sort_values()
        if not ppda_df.empty:
            st.bar_chart(ppda_df, color="#f59e0b", height=250)

        st.markdown("**Full Advanced Stats Table**")
        st.dataframe(adv_df, hide_index=True, use_container_width=True)
    else:
        st.info("Advanced stats (xG, PPDA etc.) not available yet — FBref data loads when the World Cup 2026 page is published. Basic API-Football stats shown below once matches are synced.")
        st.dataframe(adv_df[["Team", "Possession %", "Shot Acc %", "Pass Acc %", "Shots on Target", "Corners/Game"]], hide_index=True, use_container_width=True)

    # ── Advanced Player Stats ──
    st.subheader("Advanced Player Stats")

    all_player_adv = {}
    # Merge FBref player data
    for name, fdata in fbref_player.items():
        all_player_adv[name] = fdata

    # Merge base player features
    for name, stats in player_features.items():
        if name not in all_player_adv:
            all_player_adv[name] = {}
        all_player_adv[name].update({
            "team": stats.get("team", ""),
            "goals": stats.get("goals", 0),
            "assists": stats.get("assists", 0),
            "matches": stats.get("matches", 0),
            "goals_per90": stats.get("goals_per_match", 0),
            "goal_contributions_per_match": stats.get("goal_contributions_per_match", 0),
        })

    if all_player_adv:
        player_adv_rows = []
        for name, p in all_player_adv.items():
            if not p.get("team"):
                continue
            player_adv_rows.append({
                "Player": name,
                "Team": p.get("team", ""),
                "G": p.get("goals", 0),
                "A": p.get("assists", 0),
                "xG": round(p.get("xg", 0), 2),
                "xA": round(p.get("xa", 0), 2),
                "npxG": round(p.get("npxg", 0), 2),
                "Key Passes/90": round(p.get("key_passes_per90", p.get("key_passes", 0)), 2),
                "Prog Pass/90": round(p.get("progressive_passes", 0), 2),
                "Prog Carry/90": round(p.get("progressive_carries", 0), 2),
                "Pressures": round(p.get("pressures", 0), 1),
                "Aerial Won %": round(p.get("aerial_won_pct", 0), 1),
                "Def Actions/90": round(p.get("defensive_actions_per90", 0), 2),
                "G+A/M": round(p.get("goal_contributions_per_match", 0), 2),
            })

        padv_df = pd.DataFrame(player_adv_rows).sort_values("xG", ascending=False)

        f1, f2 = st.columns(2)
        teams_adv = ["All"] + sorted(padv_df["Team"].unique().tolist())
        sel_team = f1.selectbox("Filter by team", teams_adv, key="adv_team")
        sort_by = f2.selectbox("Sort by", ["xG", "xA", "Key Passes/90", "Prog Pass/90", "Def Actions/90", "G+A/M"], key="adv_sort")

        if sel_team != "All":
            padv_df = padv_df[padv_df["Team"] == sel_team]
        padv_df = padv_df.sort_values(sort_by, ascending=False)

        st.dataframe(padv_df, hide_index=True, use_container_width=True)
    else:
        st.info("No player data yet.")

# ─── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.caption("Data refreshes every 5 minutes · Sentiment model: twitter-roberta-base · Win model: logistic regression (17 features)")
if st.button("🔄 Refresh now"):
    st.cache_data.clear()
    st.rerun()
