"""Clustering unit tests with fixed embeddings."""

import numpy as np

from pulse.config import load_pipeline_config
from pulse.pipeline.clustering import cluster_reviews, cluster_score, select_cluster_samples
from pulse.pipeline.models import ScrubbedReview


def _synthetic_reviews(n: int = 60) -> list[ScrubbedReview]:
    reviews: list[ScrubbedReview] = []
    for i in range(n):
        rating = 1 if i < n // 2 else 5
        reviews.append(
            ScrubbedReview(
                text=f"Review number {i} about app experience and trading performance today",
                rating=rating,
                original_index=i,
            )
        )
    return reviews


def test_cluster_score_prioritizes_large_low_star() -> None:
    assert cluster_score(100, 1.5) > cluster_score(100, 4.5)
    assert cluster_score(50, 2.0) > cluster_score(20, 2.0)


def test_cluster_reviews_returns_clusters() -> None:
    rng = np.random.default_rng(42)
    reviews = _synthetic_reviews(80)
    embeddings = rng.normal(size=(80, 16)).astype(np.float32)
    pipeline_config = load_pipeline_config()

    result = cluster_reviews(reviews, embeddings, pipeline_config)

    assert result.clusters
    assert result.noise_count >= 0
    assert all(cluster.size >= pipeline_config.clustering.hdbscan.min_cluster_size for cluster in result.clusters)


def test_select_cluster_samples_limits_count() -> None:
    rng = np.random.default_rng(0)
    embeddings = rng.normal(size=(20, 8)).astype(np.float32)
    indices = list(range(20))
    selected = select_cluster_samples(embeddings, indices, max_samples=5)
    assert len(selected) == 5
