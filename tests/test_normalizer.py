"""Normalizer unit tests."""

from pulse.config import IngestionConfig
from pulse.ingestion.models import RawReview, Review
from pulse.ingestion.normalizer import (
    dedupe_raw_reviews,
    normalize_reviews,
    passes_quality_filters,
    rejection_reason,
    review_dedupe_key,
)


def _raw(text: str, rating: int = 4) -> RawReview:
    return RawReview(text=text, rating=rating)


def test_review_dedupe_key_stable() -> None:
    review = _raw("hello world " * 4)
    assert review_dedupe_key(review) == review_dedupe_key(review)


def test_dedupe_raw_reviews_keeps_first() -> None:
    first = _raw("duplicate review text with enough words here")
    second = _raw("duplicate review text with enough words here")
    deduped = dedupe_raw_reviews([first, second])
    assert len(deduped) == 1
    assert deduped[0].rating == 4


def test_dedupe_keeps_same_text_different_ratings() -> None:
    first = _raw("duplicate review text with enough words here", rating=4)
    second = _raw("duplicate review text with enough words here", rating=1)
    deduped = dedupe_raw_reviews([first, second])
    assert len(deduped) == 2


def test_passes_quality_filters_rejects_short_text() -> None:
    config = IngestionConfig(
        window_weeks=10,
        min_reviews=1,
        max_reviews=100,
        min_words=8,
        allowed_language="en",
    )
    assert not passes_quality_filters("too short", config)
    assert rejection_reason("too short", config) == "too_short"


def test_rejects_emoji_reviews() -> None:
    config = IngestionConfig(
        window_weeks=10,
        min_reviews=1,
        max_reviews=100,
        min_words=8,
        allowed_language="en",
    )
    text = "This review has an emoji 😀 and enough words to pass length check"
    assert rejection_reason(text, config) == "emoji"


def test_rejects_non_english_reviews() -> None:
    config = IngestionConfig(
        window_weeks=10,
        min_reviews=1,
        max_reviews=100,
        min_words=8,
        allowed_language="en",
    )
    text = "यह समीक्षा हिंदी में है और अंग्रेजी फ़िल्टर द्वारा हटा दी जानी चाहिए"
    assert rejection_reason(text, config) == "non_english"


def test_normalize_reviews_from_fixture_batch(play_store_page1) -> None:
    from pulse.ingestion.play_store import parse_scraper_batch

    config = IngestionConfig(
        window_weeks=10,
        min_reviews=1,
        max_reviews=100,
        min_words=8,
        allowed_language="en",
    )
    raw_scraped = parse_scraper_batch(play_store_page1)
    raw = [RawReview(text=r.text, rating=r.rating) for r in raw_scraped]
    normalized, stats = normalize_reviews(raw, config)
    texts = [r.text for r in normalized]
    assert any("market opens" in t for t in texts)
    assert all(len(r.text.split()) >= 8 for r in normalized)
    assert set(Review.model_fields) == {"text", "rating"}
    assert len(normalized) < len(raw)
    assert stats.dropped_too_short >= 1
    assert stats.dropped_emoji >= 1
    assert stats.dropped_non_english >= 1
