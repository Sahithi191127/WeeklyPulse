"""Review quality filters and deduplication.

Phase 1 normalization rules (applied when building reviews_normalized.json):
1. Drop reviews with fewer than min_words (default 8).
2. Drop reviews containing emoji.
3. Drop reviews not in allowed_language (default English).
"""

from __future__ import annotations

import hashlib
import logging
import random
import re

from langdetect import DetectorFactory, LangDetectException, detect

from pulse.config import IngestionConfig
from pulse.ingestion.models import NormalizationStats, RawReview, Review

DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001FAFF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]",
    flags=re.UNICODE,
)


def review_dedupe_key(review: RawReview) -> str:
    """Stable hash for deduplication: text + rating."""
    payload = f"{review.text.strip()}|{review.rating}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dedupe_raw_reviews(reviews: list[RawReview]) -> list[RawReview]:
    """Keep first occurrence per dedupe key."""
    seen: set[str] = set()
    unique: list[RawReview] = []
    for review in reviews:
        key = review_dedupe_key(review)
        if key in seen:
            continue
        seen.add(key)
        unique.append(review)
    return unique


def word_count(text: str) -> int:
    return len(text.split())


def contains_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(text))


def is_allowed_language(text: str, allowed_language: str) -> bool:
    try:
        detected = detect(text)
    except LangDetectException:
        return False
    return detected == allowed_language


def rejection_reason(text: str, config: IngestionConfig) -> str | None:
    """Return why a review fails normalization, or None if it passes."""
    cleaned = text.strip()
    if not cleaned:
        return "empty"
    if word_count(cleaned) < config.min_words:
        return "too_short"
    if contains_emoji(cleaned):
        return "emoji"
    if not is_allowed_language(cleaned, config.allowed_language):
        return "non_english"
    return None


def passes_quality_filters(text: str, config: IngestionConfig) -> bool:
    return rejection_reason(text, config) is None


def normalize_reviews(
    raw_reviews: list[RawReview],
    config: IngestionConfig,
) -> tuple[list[Review], NormalizationStats]:
    """Apply dedupe and quality filters; return canonical Review list and drop stats."""
    deduped = dedupe_raw_reviews(raw_reviews)
    normalized: list[Review] = []
    stats = NormalizationStats()

    for raw in deduped:
        if not (1 <= raw.rating <= 5):
            logger.warning("Dropping review with invalid rating: %s", raw.rating)
            stats.dropped_invalid_rating += 1
            continue

        reason = rejection_reason(raw.text, config)
        if reason == "empty":
            stats.dropped_empty += 1
            continue
        if reason == "too_short":
            stats.dropped_too_short += 1
            continue
        if reason == "emoji":
            stats.dropped_emoji += 1
            continue
        if reason == "non_english":
            stats.dropped_non_english += 1
            continue

        normalized.append(Review(text=raw.text.strip(), rating=raw.rating))

    capped = _apply_max_reviews_cap(normalized, config.max_reviews)
    stats.dropped_over_max_cap = len(normalized) - len(capped)
    return capped, stats


def _apply_max_reviews_cap(reviews: list[Review], max_reviews: int) -> list[Review]:
    if len(reviews) <= max_reviews:
        return reviews

    rng = random.Random(42)
    sampled = reviews.copy()
    rng.shuffle(sampled)
    return sampled[:max_reviews]
