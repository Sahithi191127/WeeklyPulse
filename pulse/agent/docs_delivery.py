"""Google Doc delivery via hosted MCP (Phase 4)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pulse.agent.mcp_client import HostedGoogleWorkspaceClient
from pulse.agent.models import AppendSectionResult, DocDeliveryResult, FindSectionResult
from pulse.config import REPO_ROOT
from pulse.render.models import DocSection

logger = logging.getLogger(__name__)

DOCS_DELIVERY_DIR = REPO_ROOT / "data" / "deliveries" / "docs"


class DocsDeliveryError(Exception):
    """Raised when Doc delivery fails."""


def _delivery_record_path(document_id: str, anchor: str) -> Path:
    safe_anchor = anchor.replace("/", "_")
    return DOCS_DELIVERY_DIR / document_id / f"{safe_anchor}.json"


def find_section_by_anchor(document_id: str, anchor: str) -> FindSectionResult:
    """Local idempotency lookup until hosted server exposes anchor search."""
    path = _delivery_record_path(document_id, anchor)
    if not path.is_file():
        return FindSectionResult(found=False, anchor=anchor, document_id=document_id)

    record = json.loads(path.read_text(encoding="utf-8"))
    return FindSectionResult(
        found=True,
        anchor=anchor,
        document_id=document_id,
        url=record.get("url"),
        source="local_ledger",
    )


def _write_delivery_record(
    *,
    document_id: str,
    anchor: str,
    url: str,
    content_chars: int,
    raw_response: dict | None,
) -> None:
    path = _delivery_record_path(document_id, anchor)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "anchor": anchor,
        "document_id": document_id,
        "url": url,
        "content_chars": content_chars,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "raw_response": raw_response,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_section(
    client: HostedGoogleWorkspaceClient,
    *,
    document_id: str,
    doc_section: DocSection,
    insert_at_end: bool = True,
    force: bool = False,
) -> AppendSectionResult:
    """Append plain-text section content to a Google Doc."""
    if not insert_at_end:
        logger.warning("insert_at_end=false ignored — hosted API always appends at end")

    existing = find_section_by_anchor(document_id, doc_section.anchor)
    url = HostedGoogleWorkspaceClient.get_document_url(document_id)
    if existing.found and not force:
        logger.info("Section anchor %s already delivered; skipping append", doc_section.anchor)
        return AppendSectionResult(
            appended=False,
            anchor=doc_section.anchor,
            document_id=document_id,
            url=existing.url or url,
            content_chars=len(doc_section.content),
        )

    raw_response = client.append_to_doc(doc_id=document_id, content=doc_section.content)
    _write_delivery_record(
        document_id=document_id,
        anchor=doc_section.anchor,
        url=url,
        content_chars=len(doc_section.content),
        raw_response=raw_response,
    )
    return AppendSectionResult(
        appended=True,
        anchor=doc_section.anchor,
        document_id=document_id,
        url=url,
        content_chars=len(doc_section.content),
        raw_response=raw_response,
    )


def deliver_doc_section(
    doc_section: DocSection,
    *,
    document_id: str,
    client: HostedGoogleWorkspaceClient,
    force: bool = False,
) -> DocDeliveryResult:
    """Deliver a rendered DocSection to Google Docs."""
    result = append_section(
        client,
        document_id=document_id,
        doc_section=doc_section,
        force=force,
    )
    return DocDeliveryResult(
        anchor=result.anchor,
        document_id=result.document_id,
        url=result.url,
        appended=result.appended,
        content_chars=result.content_chars,
    )
