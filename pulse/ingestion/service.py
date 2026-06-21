"""Ingestion orchestration: scrape, normalize, cache."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from pulse.config import ProductConfig, load_product_config
from pulse.ingestion.cache import (
    cache_dir_for,
    has_complete_cache,
    load_normalized_reviews,
    utc_now,
    write_cache,
    write_incomplete_manifest,
)
from pulse.ingestion.models import CacheManifest, IngestionResult, ReviewSource, RunContext
from pulse.ingestion.normalizer import dedupe_raw_reviews, normalize_reviews
from pulse.ingestion.play_store import IngestionError, PlayStoreSource

logger = logging.getLogger(__name__)


def compute_window(
    window_weeks: int,
    *,
    reference: datetime | None = None,
) -> tuple[datetime, datetime]:
    end = reference or utc_now()
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(weeks=window_weeks)
    return start, end


def ingest_product(
    product: str = "groww",
    *,
    cache_date: date | None = None,
    force_refresh: bool = False,
    source: ReviewSource | None = None,
) -> IngestionResult:
    """
    Fetch Play Store reviews, normalize, and cache.

    Reuses same-day complete cache unless force_refresh is True.
    Raises IngestionError on scrape failure or insufficient normalized reviews.
    """
    product_config = load_product_config(product)
    cache_date = cache_date or utc_now().date()
    window_weeks = product_config.ingestion.window_weeks
    window_start, window_end = compute_window(window_weeks)

    if not force_refresh and has_complete_cache(
        product, cache_date, window_weeks=window_weeks
    ):
        reviews = load_normalized_reviews(product, cache_date)
        logger.info(
            "Cache hit for %s on %s (%s normalized reviews)",
            product,
            cache_date,
            len(reviews),
        )
        return IngestionResult(
            product=product,
            cache_dir=str(cache_dir_for(product, cache_date)),
            raw_count=-1,
            normalized_count=len(reviews),
            reviews=reviews,
            from_cache=True,
        )

    scraper = source or PlayStoreSource()
    scraped_at = utc_now()

    try:
        raw_reviews = scraper.fetch_reviews(
            product_config.play_store.app_id,
            window_start=window_start,
            window_end=window_end,
            lang=product_config.play_store.lang,
            country=product_config.play_store.country,
        )
    except IngestionError:
        _write_failure_manifest(
            product_config,
            cache_date=cache_date,
            window_start=window_start,
            window_end=window_end,
            scraped_at=scraped_at,
            raw_count=0,
            normalized_count=0,
        )
        raise

    raw_reviews = dedupe_raw_reviews(raw_reviews)

    if not raw_reviews:
        _write_failure_manifest(
            product_config,
            cache_date=cache_date,
            window_start=window_start,
            window_end=window_end,
            scraped_at=scraped_at,
            raw_count=0,
            normalized_count=0,
        )
        raise IngestionError(
            f"No Play Store reviews found in the last {window_weeks} weeks for {product}"
        )

    normalized, norm_stats = normalize_reviews(raw_reviews, product_config.ingestion)
    logger.info(
        "Normalization for %s: kept=%s dropped_short=%s dropped_emoji=%s "
        "dropped_non_english=%s",
        product,
        len(normalized),
        norm_stats.dropped_too_short,
        norm_stats.dropped_emoji,
        norm_stats.dropped_non_english,
    )
    min_reviews = product_config.ingestion.min_reviews

    if len(normalized) < min_reviews:
        _write_failure_manifest(
            product_config,
            cache_date=cache_date,
            window_start=window_start,
            window_end=window_end,
            scraped_at=scraped_at,
            raw_count=len(raw_reviews),
            normalized_count=len(normalized),
        )
        raise IngestionError(
            f"Only {len(normalized)} normalized reviews (minimum {min_reviews}) for {product}"
        )

    if len(normalized) < 100:
        logger.warning(
            "Low normalized review count for %s: %s (possible filter regression)",
            product,
            len(normalized),
        )

    manifest = CacheManifest(
        product=product,
        cache_date=cache_date,
        window_weeks=window_weeks,
        window_start=window_start,
        window_end=window_end,
        status="complete",
        raw_count=len(raw_reviews),
        normalized_count=len(normalized),
        scraped_at=scraped_at,
        app_id=product_config.play_store.app_id,
        normalization=norm_stats,
    )

    cache_path = write_cache(
        product,
        cache_date,
        manifest=manifest,
        raw_reviews=raw_reviews,
        normalized_reviews=normalized,
    )

    return IngestionResult(
        product=product,
        cache_dir=str(cache_path),
        raw_count=len(raw_reviews),
        normalized_count=len(normalized),
        reviews=normalized,
        from_cache=False,
    )


def build_run_context(
    product_config: ProductConfig,
    *,
    cache_date: date | None = None,
    iso_week: str | None = None,
) -> RunContext:
    cache_date = cache_date or utc_now().date()
    window_weeks = product_config.ingestion.window_weeks
    window_start, window_end = compute_window(window_weeks)
    return RunContext(
        product=product_config.product,
        iso_week=iso_week,
        cache_date=cache_date,
        window_weeks=window_weeks,
        window_start=window_start,
        window_end=window_end,
    )


def _write_failure_manifest(
    product_config: ProductConfig,
    *,
    cache_date: date,
    window_start: datetime,
    window_end: datetime,
    scraped_at: datetime,
    raw_count: int,
    normalized_count: int,
) -> None:
    manifest = CacheManifest(
        product=product_config.product,
        cache_date=cache_date,
        window_weeks=product_config.ingestion.window_weeks,
        window_start=window_start,
        window_end=window_end,
        status="incomplete",
        raw_count=raw_count,
        normalized_count=normalized_count,
        scraped_at=scraped_at,
        app_id=product_config.play_store.app_id,
    )
    write_incomplete_manifest(manifest)
