"""Production environment and send-mode tests (Phase 9)."""

from __future__ import annotations

import httpx
import pytest

from pulse.agent.email_delivery import deliver_email_teaser
from pulse.agent.mcp_client import HostedGoogleWorkspaceClient
from pulse.config import HostedMcpConfig, load_product_config, resolve_email_mode
from pulse.render.models import EmailTeaser


@pytest.fixture
def sample_email_teaser() -> EmailTeaser:
    return EmailTeaser(
        subject="Groww Weekly Review Pulse — 2026-W24",
        theme_bullets=["Theme — summary"],
        cta_url="https://docs.google.com/document/d/doc-1/edit",
        text_body="Top themes\n\nhttps://docs.google.com/document/d/doc-1/edit",
        html_body="<p>Top themes</p>",
        footer="Generated",
        idempotency_key="groww-2026-W24-email",
    )


def test_resolve_email_mode_production_defaults_to_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_ENV", "production")
    monkeypatch.delenv("PULSE_EMAIL_MODE", raising=False)
    product_config = load_product_config("groww")
    assert resolve_email_mode(product_config) == "send"


def test_resolve_email_mode_staging_defaults_to_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_ENV", "staging")
    monkeypatch.delenv("PULSE_EMAIL_MODE", raising=False)
    product_config = load_product_config("groww")
    assert resolve_email_mode(product_config) == "draft"


def test_resolve_email_mode_env_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSE_ENV", "production")
    monkeypatch.setenv("PULSE_EMAIL_MODE", "draft")
    product_config = load_product_config("groww")
    assert resolve_email_mode(product_config) == "draft"


def test_supports_send_email_when_endpoint_advertised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "service": "google-mcp-server",
                    "endpoints": ["/append_to_doc", "/create_email_draft", "/send_email"],
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    config = HostedMcpConfig(base_url="https://example.test")
    client = HostedGoogleWorkspaceClient(
        config,
        http_client=httpx.Client(base_url="https://example.test", transport=transport),
    )
    try:
        assert client.supports_send_email() is True
    finally:
        client.close()


def test_deliver_email_teaser_send_mode(
    sample_email_teaser: EmailTeaser,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pulse.agent import email_delivery as email_delivery_module

    monkeypatch.setattr(email_delivery_module, "EMAIL_DELIVERY_DIR", tmp_path / "email")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "service": "google-mcp-server",
                    "endpoints": ["/send_email"],
                },
            )
        if request.url.path == "/send_email":
            return httpx.Response(200, json={"message_id": "msg-999"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    config = HostedMcpConfig(base_url="https://example.test")
    client = HostedGoogleWorkspaceClient(
        config,
        http_client=httpx.Client(base_url="https://example.test", transport=transport),
    )
    try:
        result = deliver_email_teaser(
            sample_email_teaser,
            recipients=["ops@example.com"],
            client=client,
            mode="send",
        )
    finally:
        client.close()

    assert result.mode == "send"
    assert result.created is True
    assert result.message_id == "msg-999"
