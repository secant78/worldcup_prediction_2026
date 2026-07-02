"""
Preprocessing pipeline for World Cup sentiment posts and match features.

Used by:
  - consumer.py       — cleans text before Bedrock/HuggingFace inference
  - features.py       — normalizes team/match features before model training
  - win_probability.py — engineers match-level features
"""
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from collections import defaultdict

# ─── Football slang normalization ──────────────────────────────────────────────

SLANG_MAP = {
    # Positive
    r"\bworldie\b":         "amazing goal",
    r"\bbanger\b":          "great goal",
    r"\bgolazo\b":          "spectacular goal",
    r"\btekkers\b":         "great skill",
    r"\bsilky\b":           "skillful",
    r"\bballed out\b":      "played brilliantly",
    r"\b(top|pure) class\b": "excellent",
    r"\bon fire\b":         "playing brilliantly",
    r"\bmagic\b":           "excellent play",
    r"\bsensational\b":     "outstanding",
    r"\blad\b":             "player",
    # Negative
    r"\bhowler\b":          "terrible mistake",
    r"\bshocker\b":         "very poor performance",
    r"\bdisaster\b":        "terrible performance",
    r"\brobbed\b":          "unfairly defeated",
    r"\brobberry\b":        "unfair result",
    r"\bparking the bus\b": "very defensive tactics",
    r"\bbottle job\b":      "collapsed under pressure",
    r"\bfraud\b":           "overrated player",
    r"\bbottled it\b":      "failed under pressure",
    r"\bbad touch\b":       "poor control",
    r"\bhoofball\b":        "long ball tactics",
    # Neutral football terms
    r"\bgaffer\b":          "manager",
    r"\bpitch\b":           "field",
    r"\bkit\b":             "uniform",
    r"\bbrace\b":           "two goals",
    r"\bhat trick\b":       "three goals",
    r"\bpen\b":             "penalty",
    r"\bpens\b":            "penalties",
    r"\bet\b":              "extra time",
    r"\baet\b":             "after extra time",
    r"\bvar\b":             "video review",
    r"\bog\b":              "own goal",
}

# Compile once
_SLANG_PATTERNS = [(re.compile(k, re.IGNORECASE), v) for k, v in SLANG_MAP.items()]

# Common football hashtags to strip (keep the text, remove the #)
_HASHTAG_STRIP = re.compile(
    r"#(worldcup2026|fifaworldcup|worldcup|fifa|football|soccer|qatar|usa|canada|mexico)\b",
    re.IGNORECASE,
)

# Patterns to remove entirely
_URL_RE       = re.compile(r"https?://\S+|www\.\S+")
_MENTION_RE   = re.compile(r"@\w+")
_HTML_RE      = re.compile(r"&[a-zA-Z]+;|&#\d+;")
_MULTI_SPACE  = re.compile(r"\s{2,}")
_EMOJI_RE     = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F9FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)

# Bot/spam username patterns
_BOT_RE = re.compile(r"bot$|^auto|^mod|_bot$|newsbot|feedbot", re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════════════════
# TEXT CLEANING
# ══════════════════════════════════════════════════════════════════════════════

def clean_text(text: str) -> str | None:
    """
    Full text cleaning pipeline. Returns None if post should be discarded.
    """
    if not text or not isinstance(text, str):
        return None

    # Decode HTML entities
    text = _HTML_RE.sub(" ", text)

    # Remove URLs
    text = _URL_RE.sub("", text)

    # Remove @mentions
    text = _MENTION_RE.sub("", text)

    # Strip football hashtags but keep others as context
    text = _HASHTAG_STRIP.sub(lambda m: m.group(1), text)
    # Remove all remaining hashtag symbols
    text = text.replace("#", "")

    # Normalize unicode (é → e etc.) but keep standard punctuation
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))

    # Remove emojis
    text = _EMOJI_RE.sub(" ", text)

    # Normalize football slang
    for pattern, replacement in _SLANG_PATTERNS:
        text = pattern.sub(replacement, text)

    # Clean up whitespace
    text = _MULTI_SPACE.sub(" ", text).strip()

    # Discard if too short (no signal)
    words = text.split()
    if len(words) < 4:
        return None

    # Discard if mostly numbers/punctuation
    alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
    if alpha_ratio < 0.4:
        return None

    return text


def is_spam(message: dict) -> bool:
    """
    Return True if a message looks like spam/bot and should be discarded.
    Checks author name, post karma, and content patterns.
    """
    author = message.get("author", "") or ""
    if _BOT_RE.search(author):
        return True

    # Very low karma Reddit posts are often spam
    score = message.get("score", 0) or 0
    if score < -10:
        return True

    text = message.get("text", "") or ""

    # Repeated characters — "GOAAAAAAAL" is fine, "aaaaaaaaa" alone is not
    if re.search(r"(.)\1{8,}", text):
        words = text.split()
        if len(words) < 3:
            return True

    return False


def detect_language(text: str) -> str:
    """
    Simple heuristic language detection — returns 'en' or 'other'.
    Only imports langdetect if available, otherwise assumes English.
    """
    try:
        from langdetect import detect
        return detect(text)
    except Exception:
        # Fallback: check for high proportion of ASCII letters
        ascii_ratio = sum(c.isascii() and c.isalpha() for c in text) / max(len(text), 1)
        return "en" if ascii_ratio > 0.7 else "other"


# ══════════════════════════════════════════════════════════════════════════════
# DEDUPLICATION
# ══════════════════════════════════════════════════════════════════════════════

class Deduplicator:
    """
    Near-duplicate detection using shingling + Jaccard similarity.
    Keeps a rolling window of recent posts to avoid memory growth.
    """
    def __init__(self, window_size: int = 1000, similarity_threshold: float = 0.8):
        self.window_size = window_size
        self.threshold = similarity_threshold
        self._recent: list[frozenset] = []

    def _shingles(self, text: str, k: int = 3) -> frozenset:
        words = text.lower().split()
        if len(words) < k:
            return frozenset(words)
        return frozenset(" ".join(words[i:i+k]) for i in range(len(words) - k + 1))

    def _jaccard(self, a: frozenset, b: frozenset) -> float:
        inter = len(a & b)
        union = len(a | b)
        return inter / union if union else 0.0

    def is_duplicate(self, text: str) -> bool:
        shingles = self._shingles(text)
        for recent in self._recent[-200:]:  # only check last 200 for speed
            if self._jaccard(shingles, recent) >= self.threshold:
                return True
        self._recent.append(shingles)
        if len(self._recent) > self.window_size:
            self._recent.pop(0)
        return False


# ══════════════════════════════════════════════════════════════════════════════
# SENTIMENT AGGREGATION
# ══════════════════════════════════════════════════════════════════════════════

def time_decay_weight(timestamp_str: str, half_life_minutes: int = 30) -> float:
    """
    Exponential time-decay weight. Posts from now = 1.0, older posts decay.
    half_life_minutes: time after which weight halves.
    """
    try:
        ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        age_minutes = (datetime.now(timezone.utc) - ts).total_seconds() / 60
        return 0.5 ** (age_minutes / half_life_minutes)
    except Exception:
        return 1.0


def engagement_weight(message: dict) -> float:
    """
    Scale weight by engagement signals (upvotes, likes, view count).
    Capped to avoid one viral post dominating.
    """
    score = message.get("score", 0) or 0
    likes = message.get("like_count", 0) or 0
    engagement = score + likes
    # Log scale, capped at 5x
    import math
    return min(1.0 + math.log1p(max(engagement, 0)) / 5, 5.0)


def classify_match_phase(timestamp_str: str, match_utc: str) -> str:
    """
    Classify a post relative to match time:
    'pre_match' (>2h before), 'live' (±2h around kickoff), 'post_match' (>2h after)
    """
    try:
        post_ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        match_ts = datetime.fromisoformat(match_utc.replace("Z", "+00:00"))
        delta_hours = (post_ts - match_ts).total_seconds() / 3600
        if delta_hours < -2:
            return "pre_match"
        elif delta_hours > 3:
            return "post_match"
        else:
            return "live"
    except Exception:
        return "unknown"


def aggregate_sentiment(posts: list[dict], use_time_decay: bool = True) -> dict:
    """
    Weighted sentiment aggregation across a list of processed posts.
    Each post must have: sentiment_score, sentiment_label, timestamp.
    Returns: avg_sentiment, weighted_sentiment, positive_ratio, negative_ratio,
             post_count, phase_breakdown.
    """
    if not posts:
        return {
            "avg_sentiment": 0.5, "weighted_sentiment": 0.5,
            "positive_ratio": 0.0, "negative_ratio": 0.0,
            "post_count": 0, "phase_breakdown": {},
        }

    total_weight = 0.0
    weighted_sum = 0.0
    label_counts: dict[str, int] = defaultdict(int)
    phase_counts: dict[str, list] = defaultdict(list)

    for post in posts:
        score = post.get("sentiment_score", 0.5)
        label = post.get("sentiment_label", "neutral")
        ts = post.get("timestamp", "")
        phase = post.get("match_phase", "unknown")

        w = 1.0
        if use_time_decay and ts:
            w *= time_decay_weight(ts)
        w *= engagement_weight(post)

        weighted_sum += score * w
        total_weight += w
        label_counts[label] += 1
        phase_counts[phase].append(score)

    n = len(posts)
    return {
        "avg_sentiment": sum(p.get("sentiment_score", 0.5) for p in posts) / n,
        "weighted_sentiment": weighted_sum / total_weight if total_weight else 0.5,
        "positive_ratio": label_counts.get("positive", 0) / n,
        "negative_ratio": label_counts.get("negative", 0) / n,
        "neutral_ratio": label_counts.get("neutral", 0) / n,
        "post_count": n,
        "phase_breakdown": {
            phase: {
                "avg": sum(scores) / len(scores),
                "count": len(scores),
            }
            for phase, scores in phase_counts.items()
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# MATCH / FEATURE PREPROCESSING
# ══════════════════════════════════════════════════════════════════════════════

def normalize_match(match: dict) -> dict:
    """
    Clean and enrich a raw match document before feature computation.
    - Imputes missing scores
    - Flags extra time / penalties
    - Adds tournament stage weight
    - Normalizes team names
    """
    m = dict(match)

    # Impute missing scores as 0
    for key in ["home_score_ft", "away_score_ft", "home_score_ht", "away_score_ht"]:
        if m.get(key) is None:
            m[key] = 0

    # Detect extra time / penalties from stage/status
    stage = (m.get("stage") or "").upper()
    m["went_to_et"]  = any(x in stage for x in ["EXTRA_TIME", "AET", "ET"])
    m["went_to_pens"] = any(x in stage for x in ["PENALTY", "PEN", "SHOOTOUT"])

    # Stage weight — knockout matches weighted more
    knockout_stages = {"ROUND_OF_16", "QUARTER_FINALS", "SEMI_FINALS", "FINAL", "THIRD_PLACE"}
    m["stage_weight"] = 1.5 if any(s in stage for s in knockout_stages) else 1.0

    # Normalize team names (handle API inconsistencies)
    m["home_team"] = _normalize_team_name(m.get("home_team", ""))
    m["away_team"] = _normalize_team_name(m.get("away_team", ""))

    return m


_TEAM_NAME_MAP = {
    "usa":                    "United States",
    "united states of america": "United States",
    "us":                     "United States",
    "usmnt":                  "United States",
    "england":                "England",
    "korea republic":         "South Korea",
    "ir iran":                "Iran",
    "czechia":                "Czech Republic",
    "türkiye":                "Turkey",
    "côte d'ivoire":          "Ivory Coast",
}

def _normalize_team_name(name: str) -> str:
    if not name:
        return name
    lower = name.lower().strip()
    return _TEAM_NAME_MAP.get(lower, name.strip())


def normalize_features(team_features: dict) -> dict:
    """
    Normalize team features to comparable scales before model training.
    - Per-90 normalization where applicable
    - Z-score normalization for outlier-sensitive features
    - Clip extreme values
    """
    import numpy as np

    if not team_features:
        return team_features

    teams = list(team_features.keys())

    # Collect arrays for z-score normalization
    def _arr(key: str) -> np.ndarray:
        return np.array([team_features[t].get(key, 0) for t in teams], dtype=float)

    def _zscore(arr: np.ndarray) -> np.ndarray:
        std = arr.std()
        return (arr - arr.mean()) / std if std > 0 else arr - arr.mean()

    # Features to z-score normalize (they have different scales)
    zscore_features = [
        "avg_goals_scored", "avg_goals_conceded", "goal_difference",
        "xg", "xga", "xg_difference",
        "avg_possession", "avg_shots_on_target", "avg_pass_accuracy",
        "progressive_passes", "progressive_carries", "pressures",
    ]

    normalized = {}
    for key in zscore_features:
        arr = _arr(key)
        z = _zscore(arr)
        for i, team in enumerate(teams):
            if team not in normalized:
                normalized[team] = {}
            normalized[team][f"{key}_z"] = float(np.clip(z[i], -3, 3))

    # Per-90 normalization for counting stats
    for i, team in enumerate(teams):
        tf = team_features[team]
        mp = max(tf.get("matches_played", 1), 1)
        if team not in normalized:
            normalized[team] = {}
        normalized[team]["goals_per_match"]     = tf.get("avg_goals_scored", 0)
        normalized[team]["conceded_per_match"]  = tf.get("avg_goals_conceded", 0)
        normalized[team]["xg_per_match"]        = tf.get("xg", 0) / mp
        normalized[team]["xga_per_match"]       = tf.get("xga", 0) / mp
        normalized[team]["shots_per_match"]     = tf.get("avg_shots_on_target", 0)

    # Merge normalized features back into originals
    result = {}
    for team in teams:
        result[team] = {**team_features[team], **normalized.get(team, {})}

    return result


def engineer_match_features(home: str, away: str, team_features: dict,
                             team_sentiment: dict, match: dict = None) -> dict:
    """
    Compute derived match-level features not in the base feature vector.
    Returns a dict of additional features to pass to the model.
    """
    hf = team_features.get(home, {})
    af = team_features.get(away, {})
    hs = team_sentiment.get(home, {})
    as_ = team_sentiment.get(away, {})

    # Head-to-head sentiment momentum
    home_sent = hs.get("avg_sentiment", 0.5)
    away_sent = as_.get("avg_sentiment", 0.5)
    sent_momentum = home_sent - away_sent

    # xG superiority ratio
    home_xg = hf.get("xg", 0) or 0
    away_xg = af.get("xg", 0) or 0
    total_xg = home_xg + away_xg
    xg_share = home_xg / total_xg if total_xg > 0 else 0.5

    # Defensive solidity score (lower conceded + higher saves + clean sheets)
    home_def = (
        (1 - hf.get("avg_goals_conceded", 1) / 3) * 0.4 +
        hf.get("clean_sheet_rate", 0) * 0.4 +
        hf.get("avg_saves", 0) / 10 * 0.2
    )
    away_def = (
        (1 - af.get("avg_goals_conceded", 1) / 3) * 0.4 +
        af.get("clean_sheet_rate", 0) * 0.4 +
        af.get("avg_saves", 0) / 10 * 0.2
    )

    # Pressing intensity (PPDA — lower = more aggressive)
    home_press = 1 / max(hf.get("ppda", 10), 0.1)
    away_press = 1 / max(af.get("ppda", 10), 0.1)

    # Tournament stage weight
    stage_weight = 1.0
    if match:
        stage = (match.get("stage") or "").upper()
        knockout = {"ROUND_OF_16", "QUARTER", "SEMI", "FINAL"}
        if any(k in stage for k in knockout):
            stage_weight = 1.5

    return {
        "sentiment_momentum": sent_momentum,
        "xg_share": xg_share,
        "home_defensive_score": home_def,
        "away_defensive_score": away_def,
        "defensive_delta": home_def - away_def,
        "home_pressing_intensity": home_press,
        "away_pressing_intensity": away_press,
        "pressing_delta": home_press - away_press,
        "stage_weight": stage_weight,
        "form_x_sentiment_home": hf.get("form_score", 1.0) * home_sent,
        "form_x_sentiment_away": af.get("form_score", 1.0) * away_sent,
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE ENTRY POINT (used by consumer.py)
# ══════════════════════════════════════════════════════════════════════════════

_deduplicator = Deduplicator()

def preprocess_message(message: dict) -> dict | None:
    """
    Full preprocessing pipeline for a single Kafka message.
    Returns enriched message dict or None if message should be discarded.
    """
    # 1. Spam filter
    if is_spam(message):
        return None

    # 2. Clean text
    cleaned = clean_text(message.get("text", ""))
    if cleaned is None:
        return None

    # 3. Language filter — English only
    if detect_language(cleaned) not in ("en", "unknown"):
        return None

    # 4. Deduplication
    if _deduplicator.is_duplicate(cleaned):
        return None

    # 5. Enrich message
    return {
        **message,
        "text": cleaned,
        "text_original": message.get("text", ""),
        "time_decay_weight": time_decay_weight(message.get("timestamp", "")),
        "engagement_weight": engagement_weight(message),
        "preprocessed": True,
    }
