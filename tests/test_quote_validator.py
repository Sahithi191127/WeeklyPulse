"""Quote validator tests."""

from pulse.pipeline.quote_validator import quote_matches_corpus, validate_quotes


def test_exact_substring_match() -> None:
    corpus = ["The app freezes exactly when the market opens, very frustrating"]
    assert quote_matches_corpus("the app freezes exactly when the market opens", corpus)


def test_ellipsis_prefix_match() -> None:
    corpus = ["The app freezes exactly when the market opens, very frustrating"]
    assert quote_matches_corpus("The app freezes exactly when the market opens...", corpus)


def test_rejects_hallucinated_quote() -> None:
    corpus = ["Support takes days to reply and does not solve the issue"]
    assert not quote_matches_corpus("The app is perfect in every way", corpus)


def test_validate_quotes_fallback_to_full_corpus() -> None:
    cluster = ["Short cluster text only here"]
    full = ["Short cluster text only here", "Another valid quote from elsewhere in corpus"]
    valid, dropped = validate_quotes(
        ["Another valid quote from elsewhere in corpus"],
        cluster_corpus=cluster,
        full_corpus=full,
    )
    assert valid == ["Another valid quote from elsewhere in corpus"]
    assert not dropped
