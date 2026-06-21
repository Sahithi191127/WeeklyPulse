"""PII scrubber tests."""

import pytest

from pulse.ingestion.models import Review
from pulse.pipeline.scrubber import scrub_reviews, scrub_text


@pytest.mark.parametrize(
    ("text", "expected_fragment"),
    [
        ("Contact me at user@example.com for help with this brokerage issue", "[EMAIL]"),
        ("Call me on +919876543210 if the app keeps crashing today", "[PHONE]"),
        ("My aadhaar 123456789012 was exposed in the ticket", "[ID]"),
        ("Paid 10k brokerage and lost 2 lakhs on this trade", "10k"),
    ],
)
def test_scrub_text_patterns(text: str, expected_fragment: str) -> None:
    result = scrub_text(text)
    assert expected_fragment in result


def test_scrub_reviews_preserves_rating() -> None:
    reviews = [Review(text="Email me at test@example.com for support on this app", rating=2)]
    scrubbed = scrub_reviews(reviews)
    assert scrubbed[0].rating == 2
    assert "[EMAIL]" in scrubbed[0].text
