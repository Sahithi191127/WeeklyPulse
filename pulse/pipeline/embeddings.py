"""Embedding generation with optional disk cache (BGE-small default, OpenAI optional)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from pulse.config import REPO_ROOT, EmbeddingConfig
from pulse.pipeline.models import ScrubbedReview

logger = logging.getLogger(__name__)

EMBEDDING_CACHE_DIR = REPO_ROOT / "data" / "cache" / "embeddings"
MAX_RETRIES = 3
DEFAULT_BGE_MODEL = "BAAI/bge-small-en-v1.5"

_model_cache: dict[str, Any] = {}


class EmbeddingClient(Protocol):
    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...


class SentenceTransformerEmbeddingClient:
    """Local embeddings via sentence-transformers (e.g. BGE-small)."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        if model_name not in _model_cache:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", model_name)
            _model_cache[model_name] = SentenceTransformer(model_name)
        self._model = _model_cache[model_name]

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        vectors = self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=min(64, len(texts)),
        )
        return np.asarray(vectors, dtype=np.float32).tolist()


class OpenAIEmbeddingClient:
    """Optional remote embeddings via OpenAI API."""

    def __init__(self, api_key: str | None = None) -> None:
        import os

        from openai import OpenAI

        self._client = OpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        response = self._client.embeddings.create(input=texts, model=model)
        return [item.embedding for item in response.data]


def create_embedding_client(config: EmbeddingConfig) -> EmbeddingClient:
    provider = config.provider.lower()
    if provider == "openai":
        return OpenAIEmbeddingClient()
    if provider in {"sentence-transformers", "huggingface", "local", "bge"}:
        return SentenceTransformerEmbeddingClient(config.model)
    raise ValueError(f"Unknown embedding provider: {config.provider}")


def embed_input_text(text: str, rating: int, *, prefix_rating: bool) -> str:
    if prefix_rating:
        return f"Rating: {rating}. {text}"
    return text


def cache_key(text: str, rating: int, *, model: str) -> str:
    payload = f"{model}|{text}|{rating}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return EMBEDDING_CACHE_DIR / f"{key}.json"


def _load_cached_vector(key: str) -> list[float] | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["vector"]


def _save_cached_vector(key: str, vector: list[float]) -> None:
    EMBEDDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(json.dumps({"vector": vector}), encoding="utf-8")


def embed_reviews(
    reviews: list[ScrubbedReview],
    config: EmbeddingConfig,
    *,
    prefix_rating: bool = True,
    client: EmbeddingClient | None = None,
) -> np.ndarray:
    """Return embedding matrix shape (n_reviews, dim)."""
    embed_client = client or create_embedding_client(config)
    vectors: list[list[float] | None] = [None] * len(reviews)
    pending_indices: list[int] = []
    pending_texts: list[str] = []

    for index, review in enumerate(reviews):
        text = embed_input_text(review.text, review.rating, prefix_rating=prefix_rating)
        key = cache_key(text, review.rating, model=config.model)
        cached = _load_cached_vector(key)
        if cached is not None:
            vectors[index] = cached
        else:
            pending_indices.append(index)
            pending_texts.append(text)

    for batch_start in range(0, len(pending_texts), config.batch_size):
        batch_indices = pending_indices[batch_start : batch_start + config.batch_size]
        batch_texts = pending_texts[batch_start : batch_start + config.batch_size]
        batch_vectors = _embed_batch_with_retry(embed_client, batch_texts, config.model)
        for idx, vector in zip(batch_indices, batch_vectors, strict=True):
            vectors[idx] = vector
            review = reviews[idx]
            key = cache_key(
                embed_input_text(review.text, review.rating, prefix_rating=prefix_rating),
                review.rating,
                model=config.model,
            )
            _save_cached_vector(key, vector)

    return np.array(vectors, dtype=np.float32)


def _embed_batch_with_retry(
    client: EmbeddingClient,
    texts: list[str],
    model: str,
) -> list[list[float]]:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return client.embed(texts, model=model)
        except Exception as exc:
            last_error = exc
            delay = 2**attempt
            logger.warning(
                "Embedding batch failed (attempt %s/%s): %s",
                attempt + 1,
                MAX_RETRIES,
                exc,
            )
            time.sleep(delay)
    raise RuntimeError(f"Embedding failed after {MAX_RETRIES} retries: {last_error}") from last_error
