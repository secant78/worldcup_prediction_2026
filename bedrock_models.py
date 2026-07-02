"""
Bedrock Claude sentiment + entity extraction.
Replaces models.py HuggingFace local inference with Claude API calls.

Falls back to local HuggingFace if Bedrock is unavailable.
"""
import json
import os

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

load_dotenv()

BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-haiku-4-5")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

_bedrock_client = None
_fallback_analyze = None


def _get_client():
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    return _bedrock_client


def _get_fallback():
    global _fallback_analyze
    if _fallback_analyze is None:
        from models import analyze as local_analyze
        _fallback_analyze = local_analyze
    return _fallback_analyze


SENTIMENT_PROMPT = """Analyze this social media post about the FIFA World Cup 2026.

Post: {text}

Respond with ONLY a JSON object in this exact format:
{{
  "sentiment_label": "positive" | "negative" | "neutral",
  "sentiment_score": <float 0.0-1.0 where 1.0=most positive>,
  "confidence": <float 0.0-1.0>,
  "entities": [
    {{"name": "<entity>", "type": "PERSON|TEAM|LOCATION|EVENT", "score": <float>}}
  ],
  "reasoning": "<one sentence explanation>"
}}

Rules:
- sentiment_score: 0.0=very negative, 0.5=neutral, 1.0=very positive
- Understand football slang: "worldie"=positive, "howler"=negative, "robbery"=negative
- Detect sarcasm accurately
- entities: only include football-relevant entities (players, teams, venues, tournaments)
- Return valid JSON only, no other text"""


def analyze(text: str) -> dict:
    """
    Analyze text sentiment and extract entities using Bedrock Claude.
    Falls back to local HuggingFace models if Bedrock is unavailable.
    """
    if not text or not text.strip():
        return {"sentiment_label": "neutral", "sentiment_score": 0.5, "entities": []}

    try:
        client = _get_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0.0,
            "messages": [{
                "role": "user",
                "content": SENTIMENT_PROMPT.format(text=text[:1000]),
            }],
        }
        response = client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result_text = json.loads(response["body"].read())["content"][0]["text"]
        parsed = json.loads(result_text)

        return {
            "sentiment_label": parsed.get("sentiment_label", "neutral"),
            "sentiment_score": float(parsed.get("sentiment_score", 0.5)),
            "confidence": float(parsed.get("confidence", 1.0)),
            "entities": parsed.get("entities", []),
            "reasoning": parsed.get("reasoning", ""),
            "source": "bedrock",
        }

    except (ClientError, NoCredentialsError) as e:
        print(f"[bedrock] AWS error, falling back to local model: {e}")
        return _get_fallback()(text)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[bedrock] Parse error, falling back to local model: {e}")
        return _get_fallback()(text)


def summarize_match_thread(posts: list[str], home_team: str, away_team: str) -> str:
    """
    Summarize fan sentiment from a collection of posts about a specific match.
    Returns a 2-3 sentence summary of what fans are saying.
    """
    if not posts:
        return ""

    sample = "\n".join(f"- {p}" for p in posts[:30])
    prompt = f"""Summarize fan sentiment about the {home_team} vs {away_team} World Cup match based on these social media posts:

{sample}

Write a 2-3 sentence summary covering:
1. Overall fan mood
2. Key talking points (standout players, controversial moments, goals)
3. Predictions or reactions

Be concise and specific. Use football terminology naturally."""

    try:
        client = _get_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 256,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(response["body"].read())["content"][0]["text"]
    except Exception as e:
        print(f"[bedrock] Summary error: {e}")
        return ""


def explain_prediction(home: str, away: str, pred: dict, team_features: dict) -> str:
    """
    Use Claude to explain the win probability prediction in plain English.
    """
    hf = team_features.get(home, {})
    af = team_features.get(away, {})

    prompt = f"""Explain this World Cup win probability prediction to a football fan in 2-3 sentences.

Match: {home} vs {away}
Prediction: {home} {pred['home_win']}% | Draw {pred['draw']}% | {away} {pred['away_win']}%

Key stats:
- {home}: form={hf.get('form_score', 0):.2f}, xG={hf.get('xg', 0):.2f}, xGA={hf.get('xga', 0):.2f}, possession={hf.get('avg_possession', 0):.0%}
- {away}: form={af.get('form_score', 0):.2f}, xG={af.get('xg', 0):.2f}, xGA={af.get('xga', 0):.2f}, possession={af.get('avg_possession', 0):.0%}

Explain WHY the model favors this outcome based on the stats. Be specific and use football language."""

    try:
        client = _get_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 200,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = client.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        return json.loads(response["body"].read())["content"][0]["text"]
    except Exception as e:
        print(f"[bedrock] Explain error: {e}")
        return ""
