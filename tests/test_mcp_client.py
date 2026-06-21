"""Tests for hosted MCP HTTP client."""

from __future__ import annotations

import httpx
import pytest

from pulse.agent.mcp_client import HostedGoogleWorkspaceClient, HostedMcpError
from pulse.config import HostedMcpConfig


def _mock_client(config: HostedMcpConfig, handler) -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(
        base_url=config.base_url,
        transport=transport,
        headers=HostedGoogleWorkspaceClient._build_headers(config),
    )


def test_health_check_and_list_docs_capabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "google-mcp-server"})
        if request.url.path == "/":
            return httpx.Response(
                200,
                json={"endpoints": ["/append_to_doc", "/create_email_draft", "/health"]},
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    config = HostedMcpConfig(base_url="https://example.test")
    with HostedGoogleWorkspaceClient(
        config, http_client=_mock_client(config, handler)
    ) as client:
        health = client.health_check()
        assert health.status == "ok"
        capabilities = client.list_docs_capabilities()
        assert "/append_to_doc" in capabilities


def test_append_to_doc_posts_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/append_to_doc":
            import json

            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)

    config = HostedMcpConfig(base_url="https://example.test")
    with HostedGoogleWorkspaceClient(
        config, http_client=_mock_client(config, handler)
    ) as client:
        result = client.append_to_doc(doc_id="doc-123", content="Hello pulse")
    assert seen["payload"] == {"doc_id": "doc-123", "content": "Hello pulse"}
    assert result["status"] == "ok"


def test_append_to_doc_sends_api_key_header() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["api_key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"status": "ok"})

    config = HostedMcpConfig(base_url="https://example.test", api_key="secret-key")
    with HostedGoogleWorkspaceClient(
        config, http_client=_mock_client(config, handler)
    ) as client:
        client.append_to_doc(doc_id="doc-123", content="body")
    assert seen["api_key"] == "secret-key"


def test_create_email_draft_posts_payload() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/create_email_draft":
            import json

            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"status": "success", "result": {"id": "d1"}})
        return httpx.Response(404)

    config = HostedMcpConfig(base_url="https://example.test")
    with HostedGoogleWorkspaceClient(
        config, http_client=_mock_client(config, handler)
    ) as client:
        result = client.create_email_draft(
            to="user@example.com",
            subject="Weekly Pulse",
            body="Hello",
        )
    assert seen["payload"] == {
        "to": "user@example.com",
        "subject": "Weekly Pulse",
        "body": "Hello",
    }
    assert result["status"] == "success"


def test_list_gmail_capabilities() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "google-mcp-server"})
        if request.url.path == "/":
            return httpx.Response(
                200,
                json={"endpoints": ["/append_to_doc", "/create_email_draft", "/health"]},
            )
        return httpx.Response(404)

    config = HostedMcpConfig(base_url="https://example.test")
    with HostedGoogleWorkspaceClient(
        config, http_client=_mock_client(config, handler)
    ) as client:
        caps = client.list_gmail_capabilities()
    assert "/create_email_draft" in caps


def test_get_document_url() -> None:
    url = HostedGoogleWorkspaceClient.get_document_url("abc123")
    assert url == "https://docs.google.com/document/d/abc123/edit"


def test_request_error_raises_hosted_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    config = HostedMcpConfig(base_url="https://example.test")
    with HostedGoogleWorkspaceClient(
        config, http_client=_mock_client(config, handler)
    ) as client:
        with pytest.raises(HostedMcpError, match="unreachable"):
            client.append_to_doc(doc_id="doc-123", content="x")
