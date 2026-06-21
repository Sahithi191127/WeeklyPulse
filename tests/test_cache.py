"""Cache I/O unit tests."""

from datetime import date, datetime, timezone

from pulse.ingestion.cache import (
    has_complete_cache,
    load_normalized_reviews,
    load_reviews,
    write_cache,
)
from pulse.ingestion.models import CacheManifest, RawReview, Review


def test_write_and_load_cache(patch_cache_root) -> None:
    product = "groww"
    cache_date = date(2026, 6, 9)
    window_start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    window_end = datetime(2026, 6, 9, tzinfo=timezone.utc)

    raw = [
        RawReview(
            text="Cached review with enough words for normalization testing",
            rating=2,
        )
    ]
    normalized = [Review(text=raw[0].text, rating=2)]
    manifest = CacheManifest(
        product=product,
        cache_date=cache_date,
        window_weeks=10,
        window_start=window_start,
        window_end=window_end,
        status="complete",
        raw_count=1,
        normalized_count=1,
        scraped_at=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc),
        app_id="com.nextbillion.groww",
    )

    write_cache(product, cache_date, manifest=manifest, raw_reviews=raw, normalized_reviews=normalized)

    assert has_complete_cache(product, cache_date, window_weeks=10)
    assert len(load_reviews(product, cache_date)) == 1
    assert len(load_normalized_reviews(product, cache_date)) == 1
