"""
YouTube Comments producer for World Cup 2026 sentiment.

Searches for World Cup match highlight videos, streams comments,
and publishes them to the Kafka topic 'youtube-comments'.

Free tier: 10,000 units/day
- search(): costs 100 units
- commentThreads(): costs 1 unit per page (~100 comments)

Set YOUTUBE_API_KEY in .env
"""
import json
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from kafka import KafkaProducer

load_dotenv()

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
KAFKA_TOPIC = "youtube-comments"
BASE_URL = "https://www.googleapis.com/youtube/v3"

SEARCH_QUERIES = [
    "World Cup 2026 highlights",
    "FIFA World Cup 2026 match",
    "WorldCup2026 goals",
]

POLL_INTERVAL = 600  # 10 minutes between search cycles


def _get(endpoint: str, params: dict) -> dict:
    params["key"] = YOUTUBE_API_KEY
    r = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def search_videos(query: str, max_results: int = 5) -> list[dict]:
    """Search for recent World Cup videos."""
    data = _get("search", {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "date",
        "maxResults": max_results,
        "relevanceLanguage": "en",
        "publishedAfter": "2026-06-01T00:00:00Z",
    })
    return data.get("items", [])


def get_comments(video_id: str, max_pages: int = 3) -> list[dict]:
    """Fetch top-level comments for a video (up to max_pages * 100 comments)."""
    comments = []
    params = {
        "part": "snippet",
        "videoId": video_id,
        "maxResults": 100,
        "order": "time",
        "textFormat": "plainText",
    }
    for _ in range(max_pages):
        try:
            data = _get("commentThreads", params)
        except requests.HTTPError as e:
            if e.response.status_code == 403:
                break  # comments disabled on this video
            raise
        for item in data.get("items", []):
            snippet = item["snippet"]["topLevelComment"]["snippet"]
            comments.append({
                "comment_id": item["id"],
                "video_id": video_id,
                "text": snippet.get("textDisplay", ""),
                "author": snippet.get("authorDisplayName", ""),
                "like_count": snippet.get("likeCount", 0),
                "published_at": snippet.get("publishedAt", ""),
            })
        next_page = data.get("nextPageToken")
        if not next_page:
            break
        params["pageToken"] = next_page

    return comments


def create_producer() -> KafkaProducer:
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    return KafkaProducer(
        bootstrap_servers=bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )


def publish_comments(producer: KafkaProducer, comments: list[dict], video_title: str, seen_ids: set):
    new_count = 0
    for comment in comments:
        cid = comment["comment_id"]
        if cid in seen_ids:
            continue
        seen_ids.add(cid)

        message = {
            "source": "youtube",
            "id": cid,
            "text": comment["text"],
            "author": comment["author"],
            "like_count": comment["like_count"],
            "video_title": video_title,
            "video_id": comment["video_id"],
            "published_at": comment["published_at"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        producer.send(KAFKA_TOPIC, value=message)
        new_count += 1

    return new_count


def run():
    if not YOUTUBE_API_KEY:
        raise ValueError("YOUTUBE_API_KEY not set in .env")

    producer = create_producer()
    seen_ids: set = set()
    seen_video_ids: set = set()

    print(f"YouTube comment producer started. Polling every {POLL_INTERVAL}s.")
    print(f"Publishing to Kafka topic: {KAFKA_TOPIC}\n")

    while True:
        total_new = 0

        for query in SEARCH_QUERIES:
            print(f"[youtube] Searching: '{query}'")
            try:
                videos = search_videos(query, max_results=5)
            except Exception as e:
                print(f"[youtube] Search error: {e}")
                continue

            for video in videos:
                video_id = video["id"]["videoId"]
                title = video["snippet"]["title"]

                if video_id not in seen_video_ids:
                    print(f"[youtube] New video: {title[:60]}")
                    seen_video_ids.add(video_id)

                try:
                    comments = get_comments(video_id, max_pages=2)
                    new = publish_comments(producer, comments, title, seen_ids)
                    if new:
                        print(f"[youtube]   +{new} new comments from '{title[:50]}'")
                    total_new += new
                except Exception as e:
                    print(f"[youtube] Error fetching comments for {video_id}: {e}")

                time.sleep(1)  # avoid quota burst

        producer.flush()
        print(f"[youtube] Cycle complete. {total_new} new comments published. Sleeping {POLL_INTERVAL}s...\n")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
