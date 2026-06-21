"""Review and run context models."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class RawReview(BaseModel):
    """All scraped reviews (cached in reviews.json)."""

    text: str
    rating: int = Field(ge=1, le=5)


class Review(BaseModel):
    """Normalized pipeline input (cached in reviews_normalized.json)."""

    text: str
    rating: int = Field(ge=1, le=5)


class RunContext(BaseModel):
    """Context for a single ingestion or pulse run."""

    product: str
    iso_week: str | None = None
    cache_date: date
    window_weeks: int
    window_start: datetime
    window_end: datetime


class NormalizationStats(BaseModel):
    """Counts of reviews removed during Phase 1 normalization."""

    dropped_too_short: int = 0
    dropped_emoji: int = 0
    dropped_non_english: int = 0
    dropped_invalid_rating: int = 0
    dropped_empty: int = 0
    dropped_over_max_cap: int = 0


class CacheManifest(BaseModel):
    """Metadata for a cached ingestion pull."""

    product: str
    cache_date: date
    window_weeks: int
    window_start: datetime
    window_end: datetime
    status: str  # complete | incomplete
    raw_count: int
    normalized_count: int
    scraped_at: datetime
    app_id: str
    normalization: NormalizationStats | None = None


class IngestionResult(BaseModel):
    """Outcome of a successful ingestion."""

    product: str
    cache_dir: str
    raw_count: int
    normalized_count: int
    reviews: list[Review]
    from_cache: bool


@runtime_checkable
class ReviewSource(Protocol):
    """Interface for review providers (Play Store v1; App Store future)."""

    def fetch_reviews(
        self,
        app_id: str,
        *,
        window_start: datetime,
        window_end: datetime,
        lang: str = "en",
        country: str = "in",
    ) -> list[RawReview]:
        """Fetch raw reviews within the date window."""
        ...
