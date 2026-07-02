from transformers import pipeline

_sentiment_pipeline = None
_ner_pipeline = None


def _get_sentiment():
    global _sentiment_pipeline
    if _sentiment_pipeline is None:
        _sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model="cardiffnlp/twitter-roberta-base-sentiment-latest",
            truncation=True,
            max_length=512,
        )
    return _sentiment_pipeline


def _get_ner():
    global _ner_pipeline
    if _ner_pipeline is None:
        _ner_pipeline = pipeline(
            "ner",
            model="dslim/bert-base-NER",
            aggregation_strategy="simple",
        )
    return _ner_pipeline


LABEL_MAP = {
    "positive": "positive",
    "negative": "negative",
    "neutral": "neutral",
}


def analyze(text: str) -> dict:
    sent_result = _get_sentiment()(text)[0]
    ner_results = _get_ner()(text)

    entities = []
    seen = set()
    for ent in ner_results:
        if ent["entity_group"] == "PER" or ent["entity_group"] == "ORG":
            word = ent["word"].strip()
            if word.lower() not in seen and len(word) > 1:
                seen.add(word.lower())
                entities.append({
                    "name": word,
                    "type": ent["entity_group"],
                    "score": round(ent["score"], 4),
                })

    return {
        "sentiment_label": LABEL_MAP.get(sent_result["label"].lower(), sent_result["label"]),
        "sentiment_score": round(sent_result["score"], 4),
        "entities": entities,
    }
