import os
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
from elasticsearch import Elasticsearch

load_dotenv()

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "worldcup-sentiment")
ES_MATCHES_INDEX = os.getenv("ES_MATCHES_INDEX", "worldcup-matches")


def get_sentiment_timeline(es, interval="15m", hours_back=24):
    query = {
        "size": 0,
        "query": {
            "range": {
                "timestamp": {
                    "gte": f"now-{hours_back}h",
                    "lte": "now",
                }
            }
        },
        "aggs": {
            "over_time": {
                "date_histogram": {
                    "field": "timestamp",
                    "fixed_interval": interval,
                },
                "aggs": {
                    "avg_score": {"avg": {"field": "sentiment_score"}},
                    "sentiment_breakdown": {
                        "terms": {"field": "sentiment_label"}
                    },
                    "top_entities": {
                        "nested": {"path": "entities"},
                        "aggs": {
                            "names": {
                                "terms": {"field": "entities.name", "size": 5}
                            }
                        },
                    },
                    "doc_count": {"value_count": {"field": "sentiment_label"}},
                },
            }
        },
    }
    return es.search(index=ES_INDEX, body=query)


def detect_spikes(es, interval="15m", hours_back=24, std_threshold=2.0):
    result = get_sentiment_timeline(es, interval, hours_back)
    buckets = result["aggregations"]["over_time"]["buckets"]

    if not buckets:
        print("No data found.")
        return []

    scores = [b["avg_score"]["value"] for b in buckets if b["avg_score"]["value"] is not None]
    if len(scores) < 3:
        print("Not enough data points for spike detection.")
        return []

    mean = sum(scores) / len(scores)
    std = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5

    spikes = []
    for bucket in buckets:
        avg = bucket["avg_score"]["value"]
        if avg is None:
            continue

        deviation = abs(avg - mean)
        if deviation > std_threshold * std:
            timestamp = bucket["key_as_string"]
            count = bucket["doc_count"]
            breakdown = {
                b["key"]: b["doc_count"]
                for b in bucket["sentiment_breakdown"]["buckets"]
            }
            top_entities = [
                e["key"]
                for e in bucket["top_entities"]["names"]["buckets"]
            ]
            direction = "positive" if avg > mean else "negative"

            spikes.append({
                "timestamp": timestamp,
                "direction": direction,
                "avg_score": round(avg, 4),
                "deviation_sigmas": round(deviation / std, 2) if std > 0 else 0,
                "post_count": count,
                "breakdown": breakdown,
                "top_entities": top_entities,
            })

    return spikes


def get_match_events_near(es, timestamp_str: str, window_minutes: int = 30) -> list[dict]:
    """Find goals and cards that occurred within window_minutes of a sentiment spike."""
    try:
        spike_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except Exception:
        return []

    before = (spike_time - timedelta(minutes=window_minutes)).isoformat()
    after = (spike_time + timedelta(minutes=window_minutes)).isoformat()

    # Query nested goals within the time window using match kickoff date as proxy
    query = {
        "size": 10,
        "query": {
            "range": {
                "utc_date": {"gte": before, "lte": after}
            }
        },
        "_source": ["home_team", "away_team", "utc_date", "goals", "cards"],
    }
    result = es.search(index=ES_MATCHES_INDEX, body=query)
    events = []
    for hit in result["hits"]["hits"]:
        src = hit["_source"]
        for goal in src.get("goals", []):
            events.append({
                "type": "GOAL",
                "match": f"{src['home_team']} vs {src['away_team']}",
                "detail": f"{goal.get('scorer')} ({goal.get('team')}) {goal.get('minute')}'",
            })
        for card in src.get("cards", []):
            events.append({
                "type": card.get("card", "CARD"),
                "match": f"{src['home_team']} vs {src['away_team']}",
                "detail": f"{card.get('player')} ({card.get('team')}) {card.get('minute')}'",
            })
    return events


def main():
    es = Elasticsearch(ES_HOST)
    print("Analyzing sentiment spikes (last 24h, 15min buckets)...\n")

    spikes = detect_spikes(es)

    if not spikes:
        print("No significant sentiment spikes detected.")
        return

    print(f"Found {len(spikes)} spike(s):\n")
    for spike in sorted(spikes, key=lambda s: s["deviation_sigmas"], reverse=True):
        print(f"  [{spike['timestamp']}]")
        print(f"    Direction:  {spike['direction']} spike ({spike['deviation_sigmas']}σ)")
        print(f"    Avg Score:  {spike['avg_score']}")
        print(f"    Posts:      {spike['post_count']}")
        print(f"    Breakdown:  {spike['breakdown']}")
        print(f"    Entities:   {', '.join(spike['top_entities']) or 'none'}")

        match_events = get_match_events_near(es, spike["timestamp"])
        if match_events:
            print(f"    Match Events near this spike:")
            for ev in match_events:
                print(f"      [{ev['type']}] {ev['match']} — {ev['detail']}")
        print()


if __name__ == "__main__":
    main()
