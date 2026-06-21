"""Tests for Google Doc delivery (Phase 4)."""

from __future__ import annotations

import json

import httpx
import pytest

from pulse.agent.docs_delivery import append_section, deliver_doc_section, find_section_by_anchor
from pulse.agent.mcp_client import HostedGoogleWorkspaceClient
from pulse.config import HostedMcpConfig
from pulse.render.models import DocSection


@pytest.fixture
def sample_doc_section() -> DocSection:
    return DocSection(
        anchor="groww-2026-W24",
        heading_text="Groww — Weekly Review Pulse — 2026-W24",
        content="Groww — Weekly Review Pulse — 2026-W24\n\nTop themes\n\n- Example theme\n",
    )


@pytest.fixture
def mock_mcp_client():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/append_to_doc":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "google-mcp-server"})
        if request.url.path == "/":
            return httpx.Response(200, json={"endpoints": ["/append_to_doc", "/health"]})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://example.test", transport=transport)
    config = HostedMcpConfig(base_url="https://example.test")
    client = HostedGoogleWorkspaceClient(config, http_client=http_client)
    yield client
    client.close()


def test_find_section_by_anchor_missing(sample_doc_section, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pulse.agent.docs_delivery.DOCS_DELIVERY_DIR", tmp_path / "docs")
    result = find_section_by_anchor("doc-1", sample_doc_section.anchor)
    assert result.found is False


def test_append_section_writes_local_idempotency_record(
    sample_doc_section, mock_mcp_client, tmp_path, monkeypatch
) -> None:
    delivery_dir = tmp_path / "docs"
    monkeypatch.setattr("pulse.agent.docs_delivery.DOCS_DELIVERY_DIR", delivery_dir)

    first = append_section(
        mock_mcp_client,
        document_id="doc-1",
        doc_section=sample_doc_section,
    )
    assert first.appended is True
    assert first.url.endswith("/document/d/doc-1/edit")

    second = append_section(
        mock_mcp_client,
        document_id="doc-1",
        doc_section=sample_doc_section,
    )
    assert second.appended is False

    record_path = delivery_dir / "doc-1" / "groww-2026-W24.json"
    assert record_path.is_file()
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["anchor"] == sample_doc_section.anchor


def test_append_section_force_reappends(
    sample_doc_section, mock_mcp_client, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("pulse.agent.docs_delivery.DOCS_DELIVERY_DIR", tmp_path / "docs")

    append_section(mock_mcp_client, document_id="doc-1", doc_section=sample_doc_section)
    forced = append_section(
        mock_mcp_client,
        document_id="doc-1",
        doc_section=sample_doc_section,
        force=True,
    )
    assert forced.appended is True


def test_deliver_doc_section(sample_doc_section, mock_mcp_client, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pulse.agent.docs_delivery.DOCS_DELIVERY_DIR", tmp_path / "docs")
    result = deliver_doc_section(
        sample_doc_section,
        document_id="doc-1",
        client=mock_mcp_client,
    )
    assert result.anchor == sample_doc_section.anchor
    assert result.content_chars == len(sample_doc_section.content)
