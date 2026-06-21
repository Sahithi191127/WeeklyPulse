"""Pipeline service integration tests with mocks."""

import json
from datetime import date

import numpy as np
import pytest

from pulse.config import load_pipeline_config, load_product_config
from pulse.ingestion.models import Review
from pulse.pipeline.service import PipelineError, run_pipeline


class _MockEmbedClient:
    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        rng = np.random.default_rng(42)
        return [rng.normal(size=16).tolist() for _ in texts]


class _MockGroqClient:
    def complete_json(self, *, system: str, user: str, model: str, max_tokens: int):
        import re

        match = re.search(r"<review[^>]*>\n(.*?)\n</review>", user, re.DOTALL)
        text_line = match.group(1).strip() if match else "brokerage charges"
        payload = {
            "theme_name": "Trading issues",
            "summary": "Users report trading problems.",
            "quotes": [text_line],
            "action_ideas": [{"title": "Fix trading", "detail": "Investigate order flow."}],
        }
        return json.dumps(payload), 120, 60


def _sample_reviews(count: int = 40) -> list[Review]:
    return [
        Review(
            text=(
                f"Review {i} mentions brokerage charges and trading lag during market hours "
                f"with detailed frustration about the mobile application experience"
            ),
            rating=1 if i % 2 == 0 else 2,
        )
        for i in range(count)
    ]


def test_run_pipeline_skip_llm() -> None:
    product_config = load_product_config("groww")
    pipeline_config = load_pipeline_config()
    report = run_pipeline(
        _sample_reviews(),
        product="groww",
        product_config=product_config,
        pipeline_config=pipeline_config,
        skip_llm=True,
        embed_client=_MockEmbedClient(),
    )
    assert report.stats.review_count == 40
    assert report.themes == []
    assert report.stats.cluster_count >= 1


def test_run_pipeline_with_mock_groq() -> None:
    product_config = load_product_config("groww")
    pipeline_config = load_pipeline_config()
    report = run_pipeline(
        _sample_reviews(60),
        product="groww",
        product_config=product_config,
        pipeline_config=pipeline_config,
        skip_llm=False,
        embed_client=_MockEmbedClient(),
        groq_client=_MockGroqClient(),
    )
    assert report.themes
    assert report.stats.groq_requests >= 1
    assert all(theme.quotes for theme in report.themes)


def test_run_pipeline_fails_below_min_reviews() -> None:
    product_config = load_product_config("groww")
    pipeline_config = load_pipeline_config()
    with pytest.raises(PipelineError, match="minimum"):
        run_pipeline(
            _sample_reviews(5),
            product="groww",
            product_config=product_config,
            pipeline_config=pipeline_config,
            skip_llm=True,
            embed_client=_MockEmbedClient(),
        )
