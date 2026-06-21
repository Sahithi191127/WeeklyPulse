"""Analysis pipeline data models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScrubbedReview(BaseModel):
    text: str
    rating: int = Field(ge=1, le=5)
    original_index: int


class ActionIdea(BaseModel):
    title: str
    detail: str


class Theme(BaseModel):
    theme_name: str
    summary: str
    quotes: list[str]
    action_ideas: list[ActionIdea]
    cluster_id: int
    cluster_size: int
    avg_rating: float
    rating_stratified: bool = False


class ClusterInfo(BaseModel):
    cluster_id: int
    indices: list[int]
    size: int
    avg_rating: float
    score: float


class PipelineStats(BaseModel):
    review_count: int
    noise_count: int
    noise_pct: float
    cluster_count: int
    groq_requests: int = 0
    groq_input_tokens: int = 0
    groq_output_tokens: int = 0
    fallbacks_used: list[str] = Field(default_factory=list)


class PulseReport(BaseModel):
    product: str
    themes: list[Theme]
    stats: PipelineStats
    cache_date: str | None = None
