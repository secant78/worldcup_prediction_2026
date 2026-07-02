import os
from dotenv import load_dotenv
from elasticsearch import Elasticsearch

load_dotenv()

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
ES_INDEX = os.getenv("ES_INDEX", "worldcup-sentiment")
ES_MATCHES_INDEX = os.getenv("ES_MATCHES_INDEX", "worldcup-matches")

MATCHES_MAPPING = {
    "mappings": {
        "properties": {
            "match_id": {"type": "integer"},
            "status": {"type": "keyword"},
            "matchday": {"type": "integer"},
            "stage": {"type": "keyword"},
            "utc_date": {"type": "date"},
            "home_team": {"type": "keyword"},
            "away_team": {"type": "keyword"},
            "home_score_ft": {"type": "integer"},
            "away_score_ft": {"type": "integer"},
            "home_score_ht": {"type": "integer"},
            "away_score_ht": {"type": "integer"},
            "winner": {"type": "keyword"},
            "last_updated": {"type": "date"},
            "goals": {
                "type": "nested",
                "properties": {
                    "minute": {"type": "integer"},
                    "team": {"type": "keyword"},
                    "scorer": {"type": "keyword"},
                    "assist": {"type": "keyword"},
                    "type": {"type": "keyword"},
                },
            },
            "cards": {
                "type": "nested",
                "properties": {
                    "minute": {"type": "integer"},
                    "team": {"type": "keyword"},
                    "player": {"type": "keyword"},
                    "card": {"type": "keyword"},
                },
            },
            "substitutions": {
                "type": "nested",
                "properties": {
                    "minute": {"type": "integer"},
                    "team": {"type": "keyword"},
                    "player_out": {"type": "keyword"},
                    "player_in": {"type": "keyword"},
                },
            },
        }
    },
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
}

INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "text": {"type": "text", "analyzer": "standard"},
            "source": {"type": "keyword"},
            "source_id": {"type": "keyword"},
            "author": {"type": "keyword"},
            "subreddit": {"type": "keyword"},
            "sentiment_label": {"type": "keyword"},
            "sentiment_score": {"type": "float"},
            "entities": {
                "type": "nested",
                "properties": {
                    "name": {"type": "keyword"},
                    "type": {"type": "keyword"},
                    "score": {"type": "float"},
                },
            },
            "timestamp": {"type": "date"},
            "processed_at": {"type": "date"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
}


def create_index():
    es = Elasticsearch(ES_HOST)

    if es.indices.exists(index=ES_INDEX):
        print(f"Index '{ES_INDEX}' already exists. Skipping.")
    else:
        es.indices.create(index=ES_INDEX, body=INDEX_MAPPING)
        print(f"Index '{ES_INDEX}' created.")

    if es.indices.exists(index=ES_MATCHES_INDEX):
        print(f"Index '{ES_MATCHES_INDEX}' already exists. Skipping.")
    else:
        es.indices.create(index=ES_MATCHES_INDEX, body=MATCHES_MAPPING)
        print(f"Index '{ES_MATCHES_INDEX}' created.")


if __name__ == "__main__":
    create_index()
