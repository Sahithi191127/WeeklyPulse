"""RunRecord and DeliveryRecord — Phase 6 run ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

RunStatus = Literal["pending", "completed", "failed"]
DeliveryChannel = Literal["google_doc", "gmail"]
EmailMode = Literal["draft", "send"]


class DeliveryRecord(BaseModel):
    """One MCP delivery tied to a run."""

    channel: DeliveryChannel
    external_id: str | None = None
    url: str | None = None
    idempotency_key: str | None = None
    created_at: datetime | None = None


class RunRecord(BaseModel):
    """Audit row for a weekly pulse run."""

    run_id: str
    product: str
    iso_week: str
    status: RunStatus
    review_count: int | None = None
    window_weeks: int | None = None
    email_mode: EmailMode | None = None
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    deliveries: list[DeliveryRecord] = Field(default_factory=list)


class DocDeliveryAudit(BaseModel):
    document_id: str
    section_anchor: str
    url: str
    appended: bool


class EmailDeliveryAudit(BaseModel):
    mode: EmailMode
    idempotency_key: str
    draft_id: str | None = None
    message_id: str | None = None
    to: str


class RunOutcome(BaseModel):
    """Orchestrator result matching Architecture §5 audit schema."""

    run_id: str
    product: str
    iso_week: str
    status: Literal["completed", "failed", "skipped"]
    skipped: bool = False
    review_count: int | None = None
    window_weeks: int | None = None
    started_at: datetime
    completed_at: datetime | None = None
    doc_delivery: DocDeliveryAudit | None = None
    email_delivery: EmailDeliveryAudit | None = None
    error_message: str | None = None
    artifact_dir: str | None = None
