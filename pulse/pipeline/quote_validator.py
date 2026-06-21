"""Quote substring validation against scrubbed review text."""

from __future__ import annotations

import logging
import re
import string

logger = logging.getLogger(__name__)


def normalize_for_match(text: str) -> str:
    cleaned = text.strip().lower()
    cleaned = cleaned.replace("…", "...")
    cleaned = re.sub(r"\s+", " ", cleaned)
    for ch in ("“", "”", "‘", "’", '"', "'"):
        cleaned = cleaned.replace(ch, "")
    return cleaned.strip(string.punctuation + " ")


def _quote_stem(quote: str) -> str:
    stem = quote.rstrip()
    while stem.endswith("..."):
        stem = stem[:-3].rstrip()
    return normalize_for_match(stem)


def quote_matches_corpus(quote: str, corpus: list[str]) -> bool:
    normalized_quote = normalize_for_match(quote)
    if not normalized_quote:
        return False

    stems = [normalized_quote]
    stem = _quote_stem(quote)
    if stem and stem != normalized_quote and len(stem) >= 20:
        stems.append(stem)

    normalized_corpus = [normalize_for_match(text) for text in corpus]
    for candidate in stems:
        for source in normalized_corpus:
            if candidate in source:
                return True
    return False


def validate_quotes(
    quotes: list[str],
    *,
    cluster_corpus: list[str],
    full_corpus: list[str],
) -> tuple[list[str], list[str]]:
    """Return (valid_quotes, dropped_reasons)."""
    valid: list[str] = []
    dropped: list[str] = []
    for quote in quotes:
        if quote_matches_corpus(quote, cluster_corpus):
            valid.append(quote)
        elif quote_matches_corpus(quote, full_corpus):
            valid.append(quote)
        else:
            dropped.append(quote)
            logger.info("quote_dropped: %s", quote[:80])
    return valid, dropped
