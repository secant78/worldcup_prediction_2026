import json
import os
from datetime import datetime, timezone

import tweepy
from dotenv import load_dotenv
from kafka import KafkaProducer

load_dotenv()

KAFKA_TOPIC = "twitter-posts"
TRACK_RULES = [
    tweepy.StreamRule("(#WorldCup2026 OR #FIFAWorldCup OR #WorldCup) lang:en"),
]


def create_producer():
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    return KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


class WorldCupStream(tweepy.StreamingClient):
    def __init__(self, bearer_token, kafka_producer):
        super().__init__(bearer_token, wait_on_rate_limit=True)
        self.producer = kafka_producer

    def on_tweet(self, tweet):
        message = {
            "source": "twitter",
            "source_id": str(tweet.id),
            "text": tweet.text[:2000],
            "author": str(tweet.author_id) if tweet.author_id else "unknown",
            "timestamp": (tweet.created_at or datetime.now(timezone.utc)).isoformat(),
        }
        self.producer.send(KAFKA_TOPIC, value=message)
        print(f"[twitter] {tweet.text[:80]}")

    def on_errors(self, errors):
        print(f"[twitter] Stream errors: {errors}")

    def on_connection_error(self):
        print("[twitter] Connection error. Reconnecting...")


def main():
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    if not bearer_token:
        print("TWITTER_BEARER_TOKEN not set. Exiting.")
        return

    producer = create_producer()
    stream = WorldCupStream(bearer_token, producer)

    existing_rules = stream.get_rules()
    if existing_rules.data:
        stream.delete_rules([r.id for r in existing_rules.data])

    for rule in TRACK_RULES:
        stream.add_rules(rule)

    print("Twitter stream started. Press Ctrl+C to stop.")
    try:
        stream.filter(tweet_fields=["created_at", "author_id"])
    except KeyboardInterrupt:
        print("\nStopping Twitter producer.")
    finally:
        stream.disconnect()
        producer.close()


if __name__ == "__main__":
    main()
