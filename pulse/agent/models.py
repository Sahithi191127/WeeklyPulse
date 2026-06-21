"""Hosted Google Workspace MCP / REST API models (Phase 4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class McpHealthStatus(BaseModel):
    status: str
    service: str
    runtime: str | None = None
    endpoints: list[str] = Field(default_factory=list)
    has_google_token: bool | None = None
    has_refresh_token: bool | None = None
    google_token_usable: bool | None = None
    google_token_error: str | None = None


class FindSectionResult(BaseModel):
    """Result of idempotency lookup before append."""

    found: bool
    anchor: str
    document_id: str
    url: str | None = None
    source: str = "local_ledger"


class AppendSectionResult(BaseModel):
    appended: bool
    anchor: str
    document_id: str
    url: str
    content_chars: int
    raw_response: dict | None = None


class DocDeliveryResult(BaseModel):
    anchor: str
    document_id: str
    url: str
    appended: bool
    content_chars: int


class EmailIdempotencyResult(BaseModel):
    already_sent: bool
    idempotency_key: str
    draft_id: str | None = None
    source: str = "local_ledger"


class CreateEmailDraftResult(BaseModel):
    created: bool
    idempotency_key: str
    to: str
    subject: str
    draft_id: str | None = None
    raw_response: dict | None = None


class SendEmailResult(BaseModel):
    sent: bool
    idempotency_key: str
    to: str
    subject: str
    message_id: str | None = None
    raw_response: dict | None = None


class EmailDeliveryResult(BaseModel):
    idempotency_key: str
    to: str
    subject: str
    mode: str = "draft"
    created: bool
    draft_id: str | None = None
    message_id: str | None = None
    doc_url: str | None = None
