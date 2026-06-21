"""Google Play Store review scraper."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from google_play_scraper import Sort, reviews
from google_play_scraper.exceptions import NotFoundError

from pulse.ingestion.models import RawReview

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 200
DEFAULT_REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 3
MAX_RAW_PAGES = 100


class IngestionError(Exception):
    """Raised when Play Store ingestion fails."""


@dataclass(frozen=True)
class _ScrapedReview:
    """Internal scrape row; published_at used only for window filtering."""

    text: str
    rating: int
    published_at: datetime


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_scraper_item(item: dict[str, Any]) -> _ScrapedReview | None:
    """Map a google-play-scraper review dict to an internal scrape row."""
    text = (item.get("content") or "").strip()
    rating = item.get("score")
    published_at = item.get("at")

    if not text:
        return None
    if not isinstance(rating, int) or not (1 <= rating <= 5):
        return None
    if not isinstance(published_at, datetime):
        return None

    return _ScrapedReview(
        text=text,
        rating=rating,
        published_at=_ensure_utc(published_at),
    )


def parse_scraper_batch(items: list[dict[str, Any]]) -> list[_ScrapedReview]:
    parsed: list[_ScrapedReview] = []
    for item in items:
        review = parse_scraper_item(item)
        if review is not None:
            parsed.append(review)
    return parsed


def to_raw_review(scraped: _ScrapedReview) -> RawReview:
    return RawReview(text=scraped.text, rating=scraped.rating)


def filter_by_window(
    reviews_list: list[_ScrapedReview],
    *,
    window_start: datetime,
    window_end: datetime,
) -> list[_ScrapedReview]:
    start = _ensure_utc(window_start)
    end = _ensure_utc(window_end)
    return [r for r in reviews_list if start <= r.published_at <= end]


class PlayStoreSource:
    """Scrape public Play Store reviews with pagination and backoff."""

    def __init__(
        self,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
        max_retries: int = MAX_RETRIES,
        max_pages: int = MAX_RAW_PAGES,
        reviews_fn: Any = reviews,
    ) -> None:
        self.page_size = page_size
        self.request_delay_seconds = request_delay_seconds
        self.max_retries = max_retries
        self.max_pages = max_pages
        self._reviews_fn = reviews_fn

    def fetch_reviews(
        self,
        app_id: str,
        *,
        window_start: datetime,
        window_end: datetime,
        lang: str = "en",
        country: str = "in",
    ) -> list[RawReview]:
        window_start = _ensure_utc(window_start)
        window_end = _ensure_utc(window_end)

        collected: list[_ScrapedReview] = []
        continuation_token = None
        pages_fetched = 0
        stop_pagination = False

        while pages_fetched < self.max_pages and not stop_pagination:
            batch, continuation_token = self._fetch_page_with_retry(
                app_id,
                lang=lang,
                country=country,
                continuation_token=continuation_token,
            )
            pages_fetched += 1

            if not batch:
                break

            parsed = parse_scraper_batch(batch)
            in_window = filter_by_window(
                parsed,
                window_start=window_start,
                window_end=window_end,
            )
            collected.extend(in_window)

            oldest_in_batch = min(
                (r.published_at for r in parsed),
                default=None,
            )
            if oldest_in_batch is not None and oldest_in_batch < window_start:
                stop_pagination = True

            if continuation_token is None:
                break

            time.sleep(self.request_delay_seconds)

        return [to_raw_review(r) for r in collected]

    def _fetch_page_with_retry(
        self,
        app_id: str,
        *,
        lang: str,
        country: str,
        continuation_token: Any,
    ) -> tuple[list[dict[str, Any]], Any]:
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                result, token = self._reviews_fn(
                    app_id,
                    lang=lang,
                    country=country,
                    sort=Sort.NEWEST,
                    count=self.page_size,
                    continuation_token=continuation_token,
                )
                return list(result), token
            except NotFoundError as exc:
                raise IngestionError(
                    f"Play Store listing not found for app_id={app_id!r} — "
                    "verify config/products/groww.yaml play_store.app_id"
                ) from exc
            except Exception as exc:
                last_error = exc
                delay = self.request_delay_seconds * (2**attempt)
                logger.warning(
                    "Play Store fetch failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries,
                    exc,
                )
                time.sleep(delay)

        raise IngestionError(
            f"Play Store fetch failed after {self.max_retries} retries: {last_error}"
        ) from last_error
