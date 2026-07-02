"""
Polls football-data.org every 60 seconds during live matches and publishes
match events (goals, cards, substitutions) to the Kafka topic 'match-events'.
Also syncs all scheduled/finished matches to Elasticsearch for correlation.
"""
import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from kafka import KafkaProducer

from football_client import (
    get_live_matches,
    get_match,
    get_matches,
    normalize_match_event,
)

load_dotenv()

KAFKA_TOPIC = "match-events"
POLL_INTERVAL = 60  # seconds between live match polls
ES_MATCHES_INDEX = os.getenv("ES_MATCHES_INDEX", "worldcup-matches")


def create_producer():
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    return KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


def create_es():
    return Elasticsearch(os.getenv("ES_HOST", "http://localhost:9200"))


def upsert_match(es, doc: dict):
    """Index or update a match document in Elasticsearch."""
    es.index(
        index=ES_MATCHES_INDEX,
        id=str(doc["match_id"]),
        document=doc,
    )


def sync_all_matches(es):
    """Sync all tournament matches to ES, fetching full detail for finished ones."""
    print("[football] Syncing all matches to ES...")
    count = 0
    for status in ["SCHEDULED", "TIMED", "LIVE", "IN_PLAY"]:
        for match in get_matches(status=status):
            doc = normalize_match_event(match)
            upsert_match(es, doc)
            count += 1

    # Fetch full match detail for finished matches to get goal scorers/cards
    print("[football] Fetching detailed data for finished matches...")
    finished = get_matches(status="FINISHED")
    for i, match in enumerate(finished):
        match_id = match.get("id")
        if not match_id:
            continue
        try:
            full = get_match(match_id)
            doc = normalize_match_event(full)
            upsert_match(es, doc)
            count += 1
            if (i + 1) % 10 == 0:
                print(f"[football]   {i + 1}/{len(finished)} finished matches synced...")
            time.sleep(0.6)  # respect rate limit (10 req/min on free tier)
        except Exception as e:
            print(f"[football] Error fetching match {match_id}: {e}")

    print(f"[football] Synced {count} matches total.")


def publish_events(producer, es, match: dict):
    """Publish new goals/cards to Kafka and upsert the match in ES."""
    doc = normalize_match_event(match)
    upsert_match(es, doc)

    for goal in doc["goals"]:
        event = {
            "event_type": "goal",
            "match_id": doc["match_id"],
            "home_team": doc["home_team"],
            "away_team": doc["away_team"],
            "utc_date": doc["utc_date"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **goal,
        }
        producer.send(KAFKA_TOPIC, value=event)
        print(f"[football] GOAL {goal['minute']}' — {goal['scorer']} ({goal['team']})")

    for card in doc["cards"]:
        event = {
            "event_type": "card",
            "match_id": doc["match_id"],
            "home_team": doc["home_team"],
            "away_team": doc["away_team"],
            "utc_date": doc["utc_date"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **card,
        }
        producer.send(KAFKA_TOPIC, value=event)
        print(f"[football] {card['card']} {card['minute']}' — {card['player']} ({card['team']})")


def main():
    producer = create_producer()
    es = create_es()

    # Sync all matches to ES on startup
    sync_all_matches(es)

    # Track event counts to detect new goals/cards
    seen_events: dict[int, dict] = {}

    print(f"\n[football] Polling for live matches every {POLL_INTERVAL}s. Press Ctrl+C to stop.\n")

    try:
        while True:
            live = get_live_matches()

            if not live:
                print(f"[football] No live matches. Waiting {POLL_INTERVAL}s...")
            else:
                print(f"[football] {len(live)} live match(es).")
                for match in live:
                    match_id = match["id"]
                    # Fetch full detail to get goals/cards
                    full = get_match(match_id)
                    prev = seen_events.get(match_id, {"goals": [], "cards": []})
                    doc = normalize_match_event(full)

                    # Only publish if new events appeared
                    if len(doc["goals"]) > len(prev["goals"]) or len(doc["cards"]) > len(prev["cards"]):
                        publish_events(producer, es, full)

                    seen_events[match_id] = doc

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n[football] Stopped.")
    finally:
        producer.close()


if __name__ == "__main__":
    main()
