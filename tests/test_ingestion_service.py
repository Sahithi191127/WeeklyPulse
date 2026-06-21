"""Ingestion service integration tests with mocked scraper."""

from datetime import datetime, timezone

import pytest

from pulse.ingestion.models import RawReview
from pulse.ingestion.play_store import IngestionError
from pulse.ingestion.service import ingest_product


def _english_review(suffix: str, rating: int) -> RawReview:
    return RawReview(
        text=(
            f"Review number {suffix} about trading performance and user experience "
            f"with detailed feedback for the application"
        ),
        rating=rating,
    )


class _MockReviewSource:
    def __init__(self, reviews: list[RawReview]) -> None:
        self._reviews = reviews

    def fetch_reviews(self, app_id: str, *, window_start, window_end, lang="en", country="in"):
        return list(self._reviews)


def test_ingest_product_writes_cache(patch_cache_root, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "pulse.ingestion.service.utc_now",
        lambda: datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
    )
    reviews = [_english_review(str(i), 2) for i in range(1, 26)]
    source = _MockReviewSource(reviews)

    result = ingest_product("groww", force_refresh=True, source=source)

    assert result.from_cache is False
    assert result.normalized_count >= 20
    assert result.raw_count == 25


def test_ingest_product_cache_hit(patch_cache_root, monkeypatch: pytest.MonkeyPatch) -> None:
    fixed_now = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("pulse.ingestion.service.utc_now", lambda: fixed_now)
    reviews = [_english_review(str(i), 3) for i in range(1, 26)]
    source = _MockReviewSource(reviews)

    first = ingest_product("groww", force_refresh=True, source=source)
    second = ingest_product("groww", force_refresh=False, source=source)

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.normalized_count == first.normalized_count


def test_ingest_product_fails_below_min_reviews(
    patch_cache_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pulse.ingestion.service.utc_now",
        lambda: datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
    )
    source = _MockReviewSource([_english_review("1", 2)])

    with pytest.raises(IngestionError, match="minimum"):
        ingest_product("groww", force_refresh=True, source=source)


def test_ingest_product_fails_empty_scrape(
    patch_cache_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pulse.ingestion.service.utc_now",
        lambda: datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc),
    )
    source = _MockReviewSource([])

    with pytest.raises(IngestionError, match="No Play Store reviews"):
        ingest_product("groww", force_refresh=True, source=source)
