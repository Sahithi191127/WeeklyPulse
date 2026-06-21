"""Review cache I/O under data/cache/{product}/{date}/."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter

from pulse.config import REPO_ROOT
from pulse.ingestion.models import CacheManifest, RawReview, Review

CACHE_ROOT = REPO_ROOT / "data" / "cache"

_REVIEWS_ADAPTER = TypeAdapter(list[RawReview])
_NORMALIZED_ADAPTER = TypeAdapter(list[Review])


def cache_dir_for(product: str, cache_date: date) -> Path:
    return CACHE_ROOT / product / cache_date.isoformat()


def find_latest_complete_cache(product: str, *, window_weeks: int) -> date | None:
    """Return the most recent cache date with a complete manifest matching window_weeks."""
    product_dir = CACHE_ROOT / product
    if not product_dir.is_dir():
        return None

    candidates: list[date] = []
    for child in product_dir.iterdir():
        if not child.is_dir():
            continue
        try:
            cache_date = date.fromisoformat(child.name)
        except ValueError:
            continue
        if has_complete_cache(product, cache_date, window_weeks=window_weeks):
            candidates.append(cache_date)

    return max(candidates) if candidates else None


def manifest_path(product: str, cache_date: date) -> Path:
    return cache_dir_for(product, cache_date) / "manifest.json"


def has_complete_cache(
    product: str,
    cache_date: date,
    *,
    window_weeks: int,
) -> bool:
    """True if a complete cache exists for product/date with matching window."""
    path = manifest_path(product, cache_date)
    if not path.is_file():
        return False
    manifest = load_manifest(product, cache_date)
    return manifest.status == "complete" and manifest.window_weeks == window_weeks


def load_manifest(product: str, cache_date: date) -> CacheManifest:
    path = manifest_path(product, cache_date)
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return CacheManifest.model_validate(data)


def load_reviews(product: str, cache_date: date) -> list[RawReview]:
    """Load all scraped reviews from reviews.json."""
    path = cache_dir_for(product, cache_date) / "reviews.json"
    with path.open(encoding="utf-8") as handle:
        return _REVIEWS_ADAPTER.validate_json(handle.read())


def load_raw_reviews(product: str, cache_date: date) -> list[RawReview]:
    """Alias for load_reviews (backwards compatibility)."""
    return load_reviews(product, cache_date)


def load_normalized_reviews(product: str, cache_date: date) -> list[Review]:
    path = cache_dir_for(product, cache_date) / "reviews_normalized.json"
    with path.open(encoding="utf-8") as handle:
        return _NORMALIZED_ADAPTER.validate_json(handle.read())


def write_cache(
    product: str,
    cache_date: date,
    *,
    manifest: CacheManifest,
    raw_reviews: list[RawReview],
    normalized_reviews: list[Review],
) -> Path:
    """Write reviews, normalized reviews, and manifest."""
    directory = cache_dir_for(product, cache_date)
    directory.mkdir(parents=True, exist_ok=True)

    reviews_path = directory / "reviews.json"
    normalized_path = directory / "reviews_normalized.json"
    manifest_file = directory / "manifest.json"

    reviews_path.write_text(
        _REVIEWS_ADAPTER.dump_json(raw_reviews, indent=2).decode("utf-8"),
        encoding="utf-8",
    )
    normalized_path.write_text(
        _NORMALIZED_ADAPTER.dump_json(normalized_reviews, indent=2).decode("utf-8"),
        encoding="utf-8",
    )
    manifest_file.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return directory


def write_incomplete_manifest(manifest: CacheManifest) -> Path:
    """Record a failed/incomplete scrape for operator visibility."""
    directory = cache_dir_for(manifest.product, manifest.cache_date)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return directory


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
