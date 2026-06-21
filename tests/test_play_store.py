"""Play Store scraper unit tests (no live network)."""

from datetime import datetime, timezone
from typing import Any

import pytest

from pulse.ingestion.models import RawReview
from pulse.ingestion.play_store import (
    IngestionError,
    PlayStoreSource,
    _ScrapedReview,
    filter_by_window,
    parse_scraper_batch,
    parse_scraper_item,
    to_raw_review,
)


def test_parse_scraper_item_valid() -> None:
    item = {
        "reviewId": "abc",
        "content": "A valid review body with enough words for testing purposes",
        "score": 4,
        "at": datetime(2026, 5, 1, tzinfo=timezone.utc),
    }
    review = parse_scraper_item(item)
    assert review is not None
    assert review.rating == 4
    raw = to_raw_review(review)
    assert raw == RawReview(text=item["content"], rating=4)


def test_parse_scraper_item_rejects_invalid_rating() -> None:
    item = {
        "content": "Some review text here with sufficient length for parser",
        "score": 0,
        "at": datetime(2026, 5, 1, tzinfo=timezone.utc),
    }
    assert parse_scraper_item(item) is None


def test_parse_scraper_batch(play_store_page1) -> None:
    parsed = parse_scraper_batch(play_store_page1)
    assert len(parsed) == 7


def test_filter_by_window() -> None:
    reviews = [
        _ScrapedReview(
            text="inside window review text with enough words here",
            rating=3,
            published_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        ),
        _ScrapedReview(
            text="outside window review text with enough words here",
            rating=2,
            published_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        ),
    ]
    filtered = filter_by_window(
        reviews,
        window_start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert len(filtered) == 1


def test_fetch_reviews_paginates_with_mock(play_store_page1) -> None:
    calls: list[Any] = []

    def mock_reviews(
        app_id: str,
        *,
        lang: str,
        country: str,
        sort: Any,
        count: int,
        continuation_token: Any,
    ):
        calls.append(continuation_token)
        if continuation_token is None:
            return play_store_page1, "token-2"
        return [], None

    source = PlayStoreSource(
        reviews_fn=mock_reviews,
        request_delay_seconds=0,
        max_pages=5,
    )
    result = source.fetch_reviews(
        "com.example.app",
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    assert len(calls) == 2
    assert len(result) >= 3
    assert all(isinstance(r, RawReview) for r in result)


def test_fetch_reviews_not_found_raises() -> None:
    from google_play_scraper.exceptions import NotFoundError

    def mock_reviews(*_args, **_kwargs):
        raise NotFoundError("app not found")

    source = PlayStoreSource(reviews_fn=mock_reviews, request_delay_seconds=0)
    with pytest.raises(IngestionError, match="listing not found"):
        source.fetch_reviews(
            "com.invalid.app",
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )


def test_fetch_reviews_retries_then_fails() -> None:
    def mock_reviews(*_args, **_kwargs):
        raise ConnectionError("network down")

    source = PlayStoreSource(
        reviews_fn=mock_reviews,
        request_delay_seconds=0,
        max_retries=2,
    )
    with pytest.raises(IngestionError, match="after 2 retries"):
        source.fetch_reviews(
            "com.example.app",
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
