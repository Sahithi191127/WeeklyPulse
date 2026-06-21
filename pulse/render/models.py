"""MCP-oriented DTOs for Doc and email delivery (Phase 3)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DocSection(BaseModel):
    """Weekly Doc section payload for MCP plain-text append."""

    anchor: str
    heading_text: str
    content: str


class EmailTeaser(BaseModel):
    """Short email payload for Gmail MCP draft/send."""

    subject: str
    theme_bullets: list[str] = Field(min_length=1, max_length=5)
    cta_label: str = "Read full report"
    cta_url: str
    text_body: str
    html_body: str
    footer: str
    idempotency_key: str
