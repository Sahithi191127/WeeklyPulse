"""UMAP + HDBSCAN clustering with mandatory fallbacks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import hdbscan
import numpy as np
import umap

from pulse.config import ClusteringConfig, PipelineConfig
from pulse.pipeline.models import ClusterInfo, ScrubbedReview

logger = logging.getLogger(__name__)

NOISE_LABEL = -1


@dataclass
class ClusteringResult:
    labels: np.ndarray
    clusters: list[ClusterInfo]
    noise_count: int
    fallbacks_used: list[str] = field(default_factory=list)
    rating_stratified: bool = False


def cluster_score(size: int, avg_rating: float) -> float:
    return size * (6.0 - avg_rating)


def _run_umap(embeddings: np.ndarray, config: ClusteringConfig) -> np.ndarray:
    n_samples = embeddings.shape[0]
    n_neighbors = min(config.umap.n_neighbors, max(2, n_samples - 1))
    reducer = umap.UMAP(
        n_neighbors=n_neighbors,
        n_components=min(config.umap.n_components, n_samples - 1),
        metric=config.umap.metric,
        random_state=42,
    )
    return reducer.fit_transform(embeddings)


def _run_hdbscan(reduced: np.ndarray, config: ClusteringConfig) -> np.ndarray:
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=config.hdbscan.min_cluster_size,
        min_samples=config.hdbscan.min_samples,
        metric="euclidean",
    )
    return clusterer.fit_predict(reduced)


def _build_clusters(
    labels: np.ndarray,
    ratings: np.ndarray,
    *,
    max_themes: int,
) -> list[ClusterInfo]:
    clusters: list[ClusterInfo] = []
    for label in sorted(set(labels.tolist())):
        if label == NOISE_LABEL:
            continue
        indices = np.where(labels == label)[0].tolist()
        if not indices:
            continue
        avg_rating = float(np.mean(ratings[indices]))
        size = len(indices)
        clusters.append(
            ClusterInfo(
                cluster_id=int(label),
                indices=indices,
                size=size,
                avg_rating=avg_rating,
                score=cluster_score(size, avg_rating),
            )
        )
    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters[:max_themes]


def _dominant_cluster_ratio(clusters: list[ClusterInfo], total: int) -> float:
    if not clusters or total == 0:
        return 0.0
    return clusters[0].size / total


def _rating_buckets(reviews: list[ScrubbedReview]) -> dict[str, list[int]]:
    buckets: dict[str, list[int]] = {"low": [], "mid": [], "high": []}
    for index, review in enumerate(reviews):
        if review.rating <= 2:
            buckets["low"].append(index)
        elif review.rating == 3:
            buckets["mid"].append(index)
        else:
            buckets["high"].append(index)
    return buckets


def _pseudo_clusters_from_buckets(
    buckets: dict[str, list[int]],
    ratings: np.ndarray,
    *,
    max_themes: int,
) -> list[ClusterInfo]:
    clusters: list[ClusterInfo] = []
    for bucket_id, (name, indices) in enumerate(buckets.items()):
        if not indices:
            continue
        avg_rating = float(np.mean(ratings[indices]))
        size = len(indices)
        clusters.append(
            ClusterInfo(
                cluster_id=bucket_id,
                indices=indices,
                size=size,
                avg_rating=avg_rating,
                score=cluster_score(size, avg_rating),
            )
        )
    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters[:max_themes]


def _cluster_subset(
    embeddings: np.ndarray,
    ratings: np.ndarray,
    indices: list[int],
    pipeline_config: PipelineConfig,
) -> tuple[np.ndarray, list[ClusterInfo]]:
    if len(indices) < pipeline_config.clustering.hdbscan.min_cluster_size:
        avg_rating = float(np.mean(ratings[indices]))
        return (
            np.full(len(indices), 0),
            [
                ClusterInfo(
                    cluster_id=0,
                    indices=indices,
                    size=len(indices),
                    avg_rating=avg_rating,
                    score=cluster_score(len(indices), avg_rating),
                )
            ],
        )

    sub_embeddings = embeddings[indices]
    sub_ratings = ratings[indices]
    reduced = _run_umap(sub_embeddings, pipeline_config.clustering)
    sub_labels = _run_hdbscan(reduced, pipeline_config.clustering)

    clusters: list[ClusterInfo] = []
    label_map: dict[int, int] = {}
    next_id = 0
    remapped = np.full(len(indices), NOISE_LABEL, dtype=int)
    for label in sorted(set(sub_labels.tolist())):
        if label == NOISE_LABEL:
            continue
        local_positions = np.where(sub_labels == label)[0].tolist()
        global_indices = [indices[pos] for pos in local_positions]
        if len(global_indices) < pipeline_config.clustering.hdbscan.min_cluster_size:
            continue
        label_map[label] = next_id
        for pos in local_positions:
            remapped[pos] = next_id
        avg_rating = float(np.mean(sub_ratings[local_positions]))
        clusters.append(
            ClusterInfo(
                cluster_id=next_id,
                indices=global_indices,
                size=len(global_indices),
                avg_rating=avg_rating,
                score=cluster_score(len(global_indices), avg_rating),
            )
        )
        next_id += 1
    clusters.sort(key=lambda c: c.score, reverse=True)
    return remapped, clusters


def cluster_reviews(
    reviews: list[ScrubbedReview],
    embeddings: np.ndarray,
    pipeline_config: PipelineConfig,
) -> ClusteringResult:
    ratings = np.array([r.rating for r in reviews], dtype=np.float32)
    fallbacks: list[str] = []

    reduced = _run_umap(embeddings, pipeline_config.clustering)
    labels = _run_hdbscan(reduced, pipeline_config.clustering)
    clusters = _build_clusters(labels, ratings, max_themes=pipeline_config.summarization.max_themes)
    noise_count = int(np.sum(labels == NOISE_LABEL))

    if not clusters:
        fallbacks.append("lower_min_cluster_size")
        lowered = pipeline_config.clustering.model_copy(
            update={
                "hdbscan": pipeline_config.clustering.hdbscan.model_copy(
                    update={
                        "min_cluster_size": max(
                            3, int(len(reviews) * 0.01),
                        )
                    }
                )
            }
        )
        labels = _run_hdbscan(reduced, lowered)
        clusters = _build_clusters(labels, ratings, max_themes=pipeline_config.summarization.max_themes)
        noise_count = int(np.sum(labels == NOISE_LABEL))

    if not clusters and pipeline_config.clustering.fallback_rating_stratify:
        fallbacks.append("rating_stratified")
        buckets = _rating_buckets(reviews)
        clusters = _pseudo_clusters_from_buckets(
            buckets,
            ratings,
            max_themes=pipeline_config.summarization.max_themes,
        )
        labels = np.full(len(reviews), NOISE_LABEL, dtype=int)
        for cluster in clusters:
            for index in cluster.indices:
                labels[index] = cluster.cluster_id
        noise_count = int(np.sum(labels == NOISE_LABEL))
        return ClusteringResult(
            labels=labels,
            clusters=clusters,
            noise_count=noise_count,
            fallbacks_used=fallbacks,
            rating_stratified=True,
        )

    if not clusters:
        raise RuntimeError("Clustering produced no valid clusters after fallbacks")

    dominant_ratio = _dominant_cluster_ratio(clusters, len(reviews))
    threshold = pipeline_config.clustering.dominant_cluster_threshold
    if dominant_ratio > threshold:
        fallbacks.append("rating_split")
        buckets = _rating_buckets(reviews)
        split_clusters: list[ClusterInfo] = []
        next_cluster_id = 0
        labels = np.full(len(reviews), NOISE_LABEL, dtype=int)
        for bucket_indices in buckets.values():
            if not bucket_indices:
                continue
            _, bucket_clusters = _cluster_subset(
                embeddings,
                ratings,
                bucket_indices,
                pipeline_config,
            )
            for cluster in bucket_clusters:
                cluster.cluster_id = next_cluster_id
                split_clusters.append(cluster)
                for index in cluster.indices:
                    labels[index] = next_cluster_id
                next_cluster_id += 1
        split_clusters.sort(key=lambda c: c.score, reverse=True)
        clusters = split_clusters[: pipeline_config.summarization.max_themes]
        noise_count = int(np.sum(labels == NOISE_LABEL))

    if len(clusters) > pipeline_config.summarization.max_themes:
        fallbacks.append("top_themes_only")
        clusters = clusters[: pipeline_config.summarization.max_themes]

    for event in fallbacks:
        logger.info("clustering_fallback: %s", event)

    return ClusteringResult(
        labels=labels,
        clusters=clusters,
        noise_count=noise_count,
        fallbacks_used=fallbacks,
    )


def select_cluster_samples(
    embeddings: np.ndarray,
    cluster_indices: list[int],
    *,
    max_samples: int,
) -> list[int]:
    if len(cluster_indices) <= max_samples:
        return list(cluster_indices)

    sub = embeddings[cluster_indices]
    centroid = np.mean(sub, axis=0)
    distances = np.linalg.norm(sub - centroid, axis=1)
    order = np.argsort(distances)

    selected: list[int] = [cluster_indices[order[0]]]
    if max_samples == 1:
        return selected

    remaining_positions = order[1:]
    step = max(1, len(remaining_positions) // (max_samples - 1))
    for position in remaining_positions[::step]:
        selected.append(cluster_indices[position])
        if len(selected) >= max_samples:
            break
    return selected[:max_samples]
