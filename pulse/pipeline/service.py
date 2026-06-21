"""Analysis pipeline orchestration."""

from __future__ import annotations

import logging
import time
from datetime import date
from pathlib import Path

from pulse.config import PipelineConfig, ProductConfig, load_pipeline_config, load_product_config
from pulse.ingestion.cache import cache_dir_for, find_latest_complete_cache, load_normalized_reviews
from pulse.ingestion.models import Review
from pulse.pipeline.clustering import cluster_reviews, select_cluster_samples
from pulse.pipeline.embeddings import EmbeddingClient, embed_reviews
from pulse.pipeline.models import PipelineStats, PulseReport, Theme
from pulse.pipeline.quote_validator import validate_quotes
from pulse.pipeline.scrubber import scrub_reviews
from pulse.pipeline.summarizer import GroqSummarizer, OpenAIGroqClient, ThemeDraft

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Raised when the analysis pipeline cannot complete."""


def run_pipeline(
    reviews: list[Review],
    *,
    product: str,
    product_config: ProductConfig,
    pipeline_config: PipelineConfig | None = None,
    cache_date: str | None = None,
    skip_llm: bool = False,
    embed_client: EmbeddingClient | None = None,
    groq_client: OpenAIGroqClient | None = None,
) -> PulseReport:
    pipeline_config = pipeline_config or load_pipeline_config()
    min_reviews = product_config.ingestion.min_reviews

    if len(reviews) < min_reviews:
        raise PipelineError(
            f"Only {len(reviews)} reviews (minimum {min_reviews}) — cannot run pipeline"
        )

    if pipeline_config.safety.scrub_pii:
        scrubbed = scrub_reviews(reviews)
    else:
        from pulse.pipeline.models import ScrubbedReview

        scrubbed = [
            ScrubbedReview(text=r.text, rating=r.rating, original_index=i)
            for i, r in enumerate(reviews)
        ]

    prefix_rating = pipeline_config.clustering.prefix_rating_in_embed
    embeddings = embed_reviews(
        scrubbed,
        pipeline_config.embedding,
        prefix_rating=prefix_rating,
        client=embed_client,
    )

    clustering = cluster_reviews(scrubbed, embeddings, pipeline_config)
    noise_pct = round(100.0 * clustering.noise_count / len(reviews), 1)
    logger.info(
        "Clustering: clusters=%s noise=%s%% fallbacks=%s",
        len(clustering.clusters),
        noise_pct,
        clustering.fallbacks_used,
    )

    stats = PipelineStats(
        review_count=len(reviews),
        noise_count=clustering.noise_count,
        noise_pct=noise_pct,
        cluster_count=len(clustering.clusters),
        fallbacks_used=clustering.fallbacks_used,
    )

    if skip_llm:
        return PulseReport(product=product, themes=[], stats=stats, cache_date=cache_date)

    summarizer = GroqSummarizer(
        config=pipeline_config.summarization,
        client=groq_client or OpenAIGroqClient(),
    )
    full_corpus = [review.text for review in scrubbed]
    themes: list[Theme] = []
    token_budget = pipeline_config.summarization.max_tokens_per_run

    for cluster_index, cluster in enumerate(clustering.clusters):
        if cluster_index > 0:
            time.sleep(pipeline_config.summarization.request_interval_seconds)

        if summarizer.usage.input_tokens + summarizer.usage.output_tokens >= token_budget:
            logger.warning("Groq token budget exhausted; stopping remaining clusters")
            break

        sample_indices = select_cluster_samples(
            embeddings,
            cluster.indices,
            max_samples=pipeline_config.summarization.max_samples_per_cluster,
        )
        samples = [scrubbed[index] for index in sample_indices]
        cluster_corpus = [scrubbed[index].text for index in cluster.indices]
        remaining = token_budget - (
            summarizer.usage.input_tokens + summarizer.usage.output_tokens
        )

        draft = summarizer.summarize_cluster(
            cluster,
            samples,
            max_review_chars=pipeline_config.safety.max_review_chars,
            remaining_token_budget=remaining,
        )
        if draft is None:
            continue

        valid_quotes, _ = validate_quotes(
            draft.quotes,
            cluster_corpus=cluster_corpus,
            full_corpus=full_corpus,
        )

        if not valid_quotes:
            reprompt_draft = summarizer.summarize_cluster(
                cluster,
                samples,
                max_review_chars=pipeline_config.safety.max_review_chars,
                remaining_token_budget=remaining,
            )
            if reprompt_draft is not None:
                valid_quotes, _ = validate_quotes(
                    reprompt_draft.quotes,
                    cluster_corpus=cluster_corpus,
                    full_corpus=full_corpus,
                )
                if valid_quotes:
                    draft = reprompt_draft

        if not valid_quotes:
            logger.warning("Omitting theme %s — no valid quotes", draft.theme_name)
            continue

        draft = ThemeDraft(
            theme_name=draft.theme_name,
            summary=draft.summary,
            quotes=valid_quotes,
            action_ideas=draft.action_ideas,
        )
        themes.append(
            Theme(
                theme_name=draft.theme_name,
                summary=draft.summary,
                quotes=draft.quotes,
                action_ideas=draft.action_ideas,
                cluster_id=cluster.cluster_id,
                cluster_size=cluster.size,
                avg_rating=cluster.avg_rating,
                rating_stratified=clustering.rating_stratified,
            )
        )

    stats.groq_requests = summarizer.usage.requests
    stats.groq_input_tokens = summarizer.usage.input_tokens
    stats.groq_output_tokens = summarizer.usage.output_tokens

    if not themes:
        raise PipelineError("No themes with validated quotes were produced")

    return PulseReport(product=product, themes=themes, stats=stats, cache_date=cache_date)


def run_pipeline_for_product(
    product: str = "groww",
    *,
    cache_date: date | None = None,
    skip_llm: bool = False,
    embed_client: EmbeddingClient | None = None,
    groq_client: OpenAIGroqClient | None = None,
) -> PulseReport:
    product_config = load_product_config(product)
    pipeline_config = load_pipeline_config()

    if cache_date is None:
        cache_date = find_latest_complete_cache(
            product,
            window_weeks=product_config.ingestion.window_weeks,
        )
    if cache_date is None:
        raise PipelineError(
            f"No complete review cache found for {product}. Run: pulse ingest --product {product}"
        )

    reviews = load_normalized_reviews(product, cache_date)
    return run_pipeline(
        reviews,
        product=product,
        product_config=product_config,
        pipeline_config=pipeline_config,
        cache_date=cache_date.isoformat(),
        skip_llm=skip_llm,
        embed_client=embed_client,
        groq_client=groq_client,
    )


def save_report_artifact(report: PulseReport, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pulse_report.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path
