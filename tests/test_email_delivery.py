"""Tests for Gmail draft delivery (Phase 5)."""

from __future__ import annotations

import json

import httpx
import pytest

from pulse.agent.email_delivery import (
    apply_doc_url_to_teaser,
    check_idempotency,
    create_email_draft,
    deliver_email_teaser,
    format_recipients,
)
from pulse.agent.mcp_client import HostedGoogleWorkspaceClient
from pulse.config import HostedMcpConfig
from pulse.render.email_teaser import DOC_DEEP_LINK_PLACEHOLDER
from pulse.render.models import EmailTeaser


@pytest.fixture
def sample_email_teaser_fixed(fixtures_dir) -> EmailTeaser:
    raw = json.loads((fixtures_dir / "expected_email_teaser.json").read_text(encoding="utf-8"))
    return EmailTeaser.model_validate(raw)


@pytest.fixture
def mock_mcp_client():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/create_email_draft":
            return httpx.Response(
                200,
                json={"status": "success", "result": {"id": "draft-123", "message": {}}},
            )
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "google-mcp-server"})
        if request.url.path == "/":
            return httpx.Response(
                200,
                json={"endpoints": ["/append_to_doc", "/create_email_draft", "/health"]},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://example.test", transport=transport)
    config = HostedMcpConfig(base_url="https://example.test")
    client = HostedGoogleWorkspaceClient(config, http_client=http_client)
    yield client
    client.close()


def test_format_recipients() -> None:
    assert format_recipients(["a@example.com", "b@example.com"]) == "a@example.com, b@example.com"


def test_apply_doc_url_to_teaser(sample_email_teaser_fixed) -> None:
    doc_url = "https://docs.google.com/document/d/abc/edit"
    updated = apply_doc_url_to_teaser(sample_email_teaser_fixed, doc_url)
    assert updated.cta_url == doc_url
    assert DOC_DEEP_LINK_PLACEHOLDER not in updated.text_body
    assert doc_url in updated.text_body


def test_check_idempotency_missing(sample_email_teaser_fixed) -> None:
    result = check_idempotency("groww-2099-W01-email")
    assert result.already_sent is False


def test_create_email_draft_writes_local_ledger(
    sample_email_teaser_fixed, mock_mcp_client, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("pulse.agent.email_delivery.EMAIL_DELIVERY_DIR", tmp_path / "email")

    first = create_email_draft(
        mock_mcp_client,
        email_teaser=sample_email_teaser_fixed,
        recipients=["lead@example.com"],
        doc_url="https://docs.google.com/document/d/abc/edit",
    )
    assert first.created is True
    assert first.draft_id == "draft-123"

    second = create_email_draft(
        mock_mcp_client,
        email_teaser=sample_email_teaser_fixed,
        recipients=["lead@example.com"],
        doc_url="https://docs.google.com/document/d/abc/edit",
    )
    assert second.created is False


def test_deliver_email_teaser(sample_email_teaser_fixed, mock_mcp_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pulse.agent.email_delivery.EMAIL_DELIVERY_DIR", tmp_path / "email")
    result = deliver_email_teaser(
        sample_email_teaser_fixed,
        recipients=["lead@example.com"],
        client=mock_mcp_client,
        doc_url="https://docs.google.com/document/d/abc/edit",
    )
    assert result.created is True
    assert result.idempotency_key == "groww-2026-W24-email"
    assert "abc" in (result.doc_url or "")
