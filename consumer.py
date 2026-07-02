import json
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from elasticsearch.helpers import bulk
from kafka import KafkaConsumer

try:
    from bedrock_models import analyze
    print("[consumer] Using Bedrock Claude for sentiment analysis.")
except Exception:
    from models import analyze
    print("[consumer] Bedrock unavailable, using local HuggingFace models.")

try:
    from preprocessor import preprocess_message as _preprocess
    _PREPROCESSOR_AVAILABLE = True
    print("[consumer] Preprocessor loaded.")
except Exception:
    _PREPROCESSOR_AVAILABLE = False
    print("[consumer] Preprocessor unavailable, skipping preprocessing.")

load_dotenv()

KAFKA_TOPICS = ["reddit-posts", "twitter-posts", "youtube-comments"]
BATCH_SIZE = 50
FLUSH_INTERVAL_SEC = 10


def create_consumer():
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    return KafkaConsumer(
        *KAFKA_TOPICS,
        bootstrap_servers=bootstrap,
        value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        group_id="sentiment-consumer",
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )


def process_message(msg: dict) -> dict:
    # Run through preprocessor: spam filter, dedup, text cleaning, language check
    if _PREPROCESSOR_AVAILABLE:
        msg = _preprocess(msg)
        if msg is None:
            return None

    text = msg.get("text", "")
    if not text:
        return None

    nlp_result = analyze(text)

    return {
        "_index": os.getenv("ES_INDEX", "worldcup-sentiment"),
        "_source": {
            "text": text,
            "source": msg.get("source", "unknown"),
            "source_id": msg.get("source_id"),
            "author": msg.get("author"),
            "subreddit": msg.get("subreddit"),
            "video_title": msg.get("video_title"),
            "sentiment_label": nlp_result["sentiment_label"],
            "sentiment_score": nlp_result["sentiment_score"],
            "entities": nlp_result["entities"],
            "match_phase": msg.get("match_phase"),
            "decay_weight": msg.get("decay_weight", 1.0),
            "engagement_weight": msg.get("engagement_weight", 1.0),
            "timestamp": msg.get("timestamp"),
            "processed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def main():
    es = Elasticsearch(os.getenv("ES_HOST", "http://localhost:9200"))
    consumer = create_consumer()
    print(f"Consumer started. Listening on {KAFKA_TOPICS}...")

    buffer = []
    last_flush = datetime.now(timezone.utc)

    try:
        for message in consumer:
            doc = process_message(message.value)
            if doc:
                buffer.append(doc)
                label = doc["_source"]["sentiment_label"]
                entities = [e["name"] for e in doc["_source"]["entities"]]
                phase = doc["_source"].get("match_phase", "")
                print(f"[{doc['_source']['source']}] {label} {phase} | entities: {entities}")

            elapsed = (datetime.now(timezone.utc) - last_flush).total_seconds()
            if len(buffer) >= BATCH_SIZE or (buffer and elapsed >= FLUSH_INTERVAL_SEC):
                success, errors = bulk(es, buffer, raise_on_error=False)
                print(f"Indexed {success} docs. Errors: {len(errors)}")
                buffer = []
                last_flush = datetime.now(timezone.utc)

    except KeyboardInterrupt:
        if buffer:
            success, _ = bulk(es, buffer, raise_on_error=False)
            print(f"Final flush: indexed {success} docs.")
        print("\nConsumer stopped.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
