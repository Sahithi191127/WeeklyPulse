"""Groq summarizer tests with mock client."""

import json

import pytest

from pulse.config import load_pipeline_config
from pulse.pipeline.models import ClusterInfo, ScrubbedReview
from pulse.pipeline.summarizer import GroqSummarizer


class _MockGroqClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def complete_json(self, *, system: str, user: str, model: str, max_tokens: int):
        self.calls += 1
        return json.dumps(self._payload), 100, 50


def test_summarizer_returns_theme_draft() -> None:
    payload = {
        "theme_name": "Brokerage charges",
        "summary": "Users complain about high fees.",
        "quotes": ["brokerage are very high don't use this app"],
        "action_ideas": [{"title": "Review pricing", "detail": "Benchmark against peers."}],
    }
    client = _MockGroqClient(payload)
    summarizer = GroqSummarizer(config=load_pipeline_config().summarization, client=client)
    cluster = ClusterInfo(cluster_id=0, indices=[0], size=10, avg_rating=1.5, score=45.0)
    samples = [
        ScrubbedReview(
            text="brokerage are very high don't use this app for trading anymore",
            rating=1,
            original_index=0,
        )
    ]

    draft = summarizer.summarize_cluster(
        cluster,
        samples,
        max_review_chars=500,
        remaining_token_budget=5000,
    )

    assert draft is not None
    assert draft.theme_name == "Brokerage charges"
    assert client.calls == 1


class _RateLimitGroqClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    def complete_json(self, *, system: str, user: str, model: str, max_tokens: int):
        self.calls += 1
        if self.calls <= 2:
            raise Exception("429 Too Many Requests — rate limit exceeded")
        return json.dumps(self._payload), 100, 50


def test_summarizer_retries_on_groq_429(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "theme_name": "Brokerage charges",
        "summary": "Users complain about high fees.",
        "quotes": ["brokerage are very high don't use this app"],
        "action_ideas": [{"title": "Review pricing", "detail": "Benchmark against peers."}],
    }
    client = _RateLimitGroqClient(payload)
    monkeypatch.setattr("pulse.pipeline.summarizer.time.sleep", lambda _s: None)
    summarizer = GroqSummarizer(config=load_pipeline_config().summarization, client=client)
    cluster = ClusterInfo(cluster_id=0, indices=[0], size=10, avg_rating=1.5, score=45.0)
    samples = [
        ScrubbedReview(
            text="brokerage are very high don't use this app for trading anymore",
            rating=1,
            original_index=0,
        )
    ]

    draft = summarizer.summarize_cluster(
        cluster,
        samples,
        max_review_chars=500,
        remaining_token_budget=5000,
    )

    assert draft is not None
    assert client.calls == 3
