"""Gmail draft delivery via hosted MCP (Phase 5)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from pulse.agent.mcp_client import HostedGoogleWorkspaceClient
from pulse.agent.models import (
    CreateEmailDraftResult,
    EmailDeliveryResult,
    EmailIdempotencyResult,
    SendEmailResult,
)
from pulse.config import EmailMode, REPO_ROOT
from pulse.render.email_teaser import DOC_DEEP_LINK_PLACEHOLDER
from pulse.render.models import EmailTeaser

logger = logging.getLogger(__name__)

EMAIL_DELIVERY_DIR = REPO_ROOT / "data" / "deliveries" / "email"


class EmailDeliveryError(Exception):
    """Raised when email delivery fails."""


def _delivery_record_path(idempotency_key: str) -> Path:
    safe_key = idempotency_key.replace("/", "_")
    return EMAIL_DELIVERY_DIR / f"{safe_key}.json"


def check_idempotency(idempotency_key: str) -> EmailIdempotencyResult:
    """Local idempotency lookup until hosted server exposes check_idempotency."""
    path = _delivery_record_path(idempotency_key)
    if not path.is_file():
        return EmailIdempotencyResult(already_sent=False, idempotency_key=idempotency_key)

    record = json.loads(path.read_text(encoding="utf-8"))
    return EmailIdempotencyResult(
        already_sent=True,
        idempotency_key=idempotency_key,
        draft_id=record.get("draft_id"),
        source="local_ledger",
    )


def _write_delivery_record(
    *,
    idempotency_key: str,
    to: str,
    subject: str,
    draft_id: str | None,
    doc_url: str | None,
    raw_response: dict | None,
) -> None:
    path = _delivery_record_path(idempotency_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "idempotency_key": idempotency_key,
        "to": to,
        "subject": subject,
        "draft_id": draft_id,
        "doc_url": doc_url,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": raw_response,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _extract_draft_id(raw_response: dict | None) -> str | None:
    if not raw_response:
        return None
    result = raw_response.get("result")
    if isinstance(result, dict) and result.get("id"):
        return str(result["id"])
    if raw_response.get("draft_id"):
        return str(raw_response["draft_id"])
    return None


def format_recipients(recipients: list[str]) -> str:
    """Hosted MCPServer accepts a single `to` string (comma-separated emails)."""
    return ", ".join(recipients)


def apply_doc_url_to_teaser(email_teaser: EmailTeaser, doc_url: str) -> EmailTeaser:
    """Replace Doc URL placeholder with the real link from Phase 4 delivery."""
    if doc_url in email_teaser.text_body and email_teaser.cta_url == doc_url:
        return email_teaser

    def _replace(value: str) -> str:
        return value.replace(DOC_DEEP_LINK_PLACEHOLDER, doc_url)

    return EmailTeaser(
        subject=email_teaser.subject,
        theme_bullets=email_teaser.theme_bullets,
        cta_label=email_teaser.cta_label,
        cta_url=doc_url,
        text_body=_replace(email_teaser.text_body),
        html_body=_replace(email_teaser.html_body),
        footer=_replace(email_teaser.footer),
        idempotency_key=email_teaser.idempotency_key,
    )


def resolve_doc_url(*, document_id: str, anchor: str | None = None) -> str:
    """Use Doc delivery ledger URL if available, else generic edit link."""
    if anchor:
        from pulse.agent.docs_delivery import find_section_by_anchor

        found = find_section_by_anchor(document_id, anchor)
        if found.found and found.url:
            return found.url
    return HostedGoogleWorkspaceClient.get_document_url(document_id)


def create_email_draft(
    client: HostedGoogleWorkspaceClient,
    *,
    email_teaser: EmailTeaser,
    recipients: list[str],
    doc_url: str | None = None,
    force: bool = False,
) -> CreateEmailDraftResult:
    """Create a Gmail draft via hosted MCP."""
    teaser = apply_doc_url_to_teaser(email_teaser, doc_url) if doc_url else email_teaser
    to = format_recipients(recipients)
    existing = check_idempotency(teaser.idempotency_key)
    if existing.already_sent and not force:
        logger.info(
            "Email idempotency key %s already delivered; skipping draft",
            teaser.idempotency_key,
        )
        return CreateEmailDraftResult(
            created=False,
            idempotency_key=teaser.idempotency_key,
            to=to,
            subject=teaser.subject,
            draft_id=existing.draft_id,
        )

    raw_response = client.create_email_draft(
        to=to,
        subject=teaser.subject,
        body=teaser.text_body,
    )
    draft_id = _extract_draft_id(raw_response)
    _write_delivery_record(
        idempotency_key=teaser.idempotency_key,
        to=to,
        subject=teaser.subject,
        draft_id=draft_id,
        doc_url=doc_url or teaser.cta_url,
        raw_response=raw_response,
    )
    return CreateEmailDraftResult(
        created=True,
        idempotency_key=teaser.idempotency_key,
        to=to,
        subject=teaser.subject,
        draft_id=draft_id,
        raw_response=raw_response,
    )


def _extract_message_id(raw_response: dict | None) -> str | None:
    if not raw_response:
        return None
    if raw_response.get("message_id"):
        return str(raw_response["message_id"])
    result = raw_response.get("result")
    if isinstance(result, dict):
        if result.get("id"):
            return str(result["id"])
        message = result.get("message")
        if isinstance(message, dict) and message.get("id"):
            return str(message["id"])
    return None


def send_email(
    client: HostedGoogleWorkspaceClient,
    *,
    email_teaser: EmailTeaser,
    recipients: list[str],
    doc_url: str | None = None,
    force: bool = False,
) -> SendEmailResult:
    """Send stakeholder email via hosted MCP (requires POST /send_email)."""
    if not client.supports_send_email():
        raise EmailDeliveryError(
            "Hosted MCP does not expose send_email — use draft mode or upgrade MCPServer"
        )

    teaser = apply_doc_url_to_teaser(email_teaser, doc_url) if doc_url else email_teaser
    to = format_recipients(recipients)
    existing = check_idempotency(teaser.idempotency_key)
    if existing.already_sent and not force:
        logger.info(
            "Email idempotency key %s already delivered; skipping send",
            teaser.idempotency_key,
        )
        return SendEmailResult(
            sent=False,
            idempotency_key=teaser.idempotency_key,
            to=to,
            subject=teaser.subject,
            message_id=existing.draft_id,
        )

    raw_response = client.send_email(
        to=to,
        subject=teaser.subject,
        body=teaser.text_body,
    )
    message_id = _extract_message_id(raw_response)
    _write_delivery_record(
        idempotency_key=teaser.idempotency_key,
        to=to,
        subject=teaser.subject,
        draft_id=message_id,
        doc_url=doc_url or teaser.cta_url,
        raw_response=raw_response,
    )
    return SendEmailResult(
        sent=True,
        idempotency_key=teaser.idempotency_key,
        to=to,
        subject=teaser.subject,
        message_id=message_id,
        raw_response=raw_response,
    )


def deliver_email_teaser(
    email_teaser: EmailTeaser,
    *,
    recipients: list[str],
    client: HostedGoogleWorkspaceClient,
    doc_url: str | None = None,
    force: bool = False,
    mode: EmailMode = "draft",
) -> EmailDeliveryResult:
    """Deliver EmailTeaser as Gmail draft or send (when MCP supports send)."""
    if not recipients:
        raise EmailDeliveryError("No email recipients configured")

    invalid = [address for address in recipients if not re.search(r"@", address)]
    if invalid:
        raise EmailDeliveryError(f"Invalid recipient addresses: {invalid}")

    if mode == "send":
        result = send_email(
            client,
            email_teaser=email_teaser,
            recipients=recipients,
            doc_url=doc_url,
            force=force,
        )
        resolved_doc_url = doc_url or email_teaser.cta_url
        return EmailDeliveryResult(
            idempotency_key=result.idempotency_key,
            to=result.to,
            subject=result.subject,
            mode="send",
            created=result.sent,
            message_id=result.message_id,
            doc_url=resolved_doc_url,
        )

    result = create_email_draft(
        client,
        email_teaser=email_teaser,
        recipients=recipients,
        doc_url=doc_url,
        force=force,
    )
    resolved_doc_url = doc_url
    if resolved_doc_url is None and DOC_DEEP_LINK_PLACEHOLDER not in email_teaser.cta_url:
        resolved_doc_url = email_teaser.cta_url

    return EmailDeliveryResult(
        idempotency_key=result.idempotency_key,
        to=result.to,
        subject=result.subject,
        mode="draft",
        created=result.created,
        draft_id=result.draft_id,
        doc_url=resolved_doc_url,
    )
