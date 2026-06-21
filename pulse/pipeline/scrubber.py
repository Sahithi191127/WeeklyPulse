"""PII redaction before embedding, LLM, and publishing."""

from __future__ import annotations

import re

from pulse.ingestion.models import Review
from pulse.pipeline.models import ScrubbedReview

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"(?:\+91[\s-]?)?[6-9]\d{9}\b")
_ID_RE = re.compile(r"\b\d{10,12}\b")
_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def scrub_text(text: str) -> str:
    """Redact PII patterns; keep financial amounts as theme signals."""
    cleaned = _EMAIL_RE.sub("[EMAIL]", text)
    cleaned = _PHONE_RE.sub("[PHONE]", cleaned)
    cleaned = _ID_RE.sub("[ID]", cleaned)

    def _redact_url(match: re.Match[str]) -> str:
        url = match.group(0)
        scheme_end = url.find("://")
        if scheme_end == -1:
            return "[URL]"
        domain_end = url.find("/", scheme_end + 3)
        if domain_end == -1:
            return url.split("?")[0]
        return url[:domain_end] + "/[REDACTED]"

    cleaned = _URL_RE.sub(_redact_url, cleaned)
    return cleaned.strip()


def scrub_reviews(reviews: list[Review]) -> list[ScrubbedReview]:
    return [
        ScrubbedReview(
            text=scrub_text(review.text),
            rating=review.rating,
            original_index=index,
        )
        for index, review in enumerate(reviews)
    ]
