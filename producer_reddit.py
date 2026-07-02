import json
import os
import time
from datetime import datetime, timezone

import praw
from dotenv import load_dotenv
from kafka import KafkaProducer

load_dotenv()

SUBREDDITS = ["soccer", "worldcup", "football"]
HASHTAG_KEYWORDS = ["world cup", "worldcup", "fifa", "worldcup2026"]
KAFKA_TOPIC = "reddit-posts"


def create_producer():
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    return KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


def create_reddit():
    return praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID"),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
        user_agent=os.getenv("REDDIT_USER_AGENT", "worldcup-sentiment-tracker/1.0"),
    )


def is_relevant(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in HASHTAG_KEYWORDS)


def stream_submissions(reddit, producer):
    subreddit = reddit.subreddit("+".join(SUBREDDITS))
    print(f"Streaming from r/{'+'.join(SUBREDDITS)}...")

    for submission in subreddit.stream.submissions(skip_existing=True):
        text = f"{submission.title} {submission.selftext}".strip()
        if not is_relevant(text):
            continue

        message = {
            "source": "reddit",
            "source_id": submission.id,
            "text": text[:2000],
            "author": str(submission.author) if submission.author else "[deleted]",
            "subreddit": submission.subreddit.display_name,
            "timestamp": datetime.fromtimestamp(
                submission.created_utc, tz=timezone.utc
            ).isoformat(),
        }
        producer.send(KAFKA_TOPIC, value=message)
        print(f"[reddit] {submission.subreddit}: {submission.title[:80]}")


def stream_comments(reddit, producer):
    subreddit = reddit.subreddit("+".join(SUBREDDITS))
    print(f"Streaming comments from r/{'+'.join(SUBREDDITS)}...")

    for comment in subreddit.stream.comments(skip_existing=True):
        text = comment.body.strip()
        if not is_relevant(text) or len(text) < 10:
            continue

        message = {
            "source": "reddit",
            "source_id": comment.id,
            "text": text[:2000],
            "author": str(comment.author) if comment.author else "[deleted]",
            "subreddit": comment.subreddit.display_name,
            "timestamp": datetime.fromtimestamp(
                comment.created_utc, tz=timezone.utc
            ).isoformat(),
        }
        producer.send(KAFKA_TOPIC, value=message)


if __name__ == "__main__":
    reddit = create_reddit()
    producer = create_producer()
    print("Reddit producer started. Press Ctrl+C to stop.")
    try:
        stream_submissions(reddit, producer)
    except KeyboardInterrupt:
        print("\nStopping Reddit producer.")
    finally:
        producer.close()
