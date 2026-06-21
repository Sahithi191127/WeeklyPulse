"""HTTP client for hosted Google Workspace server on Railway (Phase 4)."""

from __future__ import annotations

import logging
import time
from typing import Any, Protocol

import httpx

from pulse.agent.models import McpHealthStatus
from pulse.config import HostedMcpConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.5


class HostedMcpTransport(Protocol):
    def health_check(self) -> McpHealthStatus: ...

    def append_to_doc(self, *, doc_id: str, content: str) -> dict[str, Any]: ...

    def create_email_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]: ...

    def send_email(self, *, to: str, subject: str, body: str) -> dict[str, Any]: ...

    def supports_send_email(self) -> bool: ...


class HostedMcpError(Exception):
    """Raised when the hosted MCP server returns an error."""


class HostedGoogleWorkspaceClient:
    """REST client for `https://web-production-c5ea8.up.railway.app`."""

    def __init__(
        self,
        config: HostedMcpConfig,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=DEFAULT_TIMEOUT,
            headers=self._build_headers(config),
        )

    @staticmethod
    def _build_headers(config: HostedMcpConfig) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if config.api_key:
            headers["X-API-Key"] = config.api_key
        return headers

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HostedGoogleWorkspaceClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def health_check(self) -> McpHealthStatus:
        response = self._request("GET", self.config.health_path)
        payload = response.json()
        config_block = payload.get("config") or {}
        endpoints = list(payload.get("endpoints") or [])
        if not endpoints:
            endpoints = self._fetch_advertised_endpoints()
        return McpHealthStatus(
            status=str(payload.get("status", "unknown")),
            service=str(payload.get("service", "unknown")),
            runtime=payload.get("runtime"),
            endpoints=endpoints,
            has_google_token=config_block.get("has_google_token"),
            has_refresh_token=config_block.get("has_refresh_token"),
            google_token_usable=config_block.get("google_token_usable"),
            google_token_error=config_block.get("google_token_error"),
        )

    def _fetch_advertised_endpoints(self) -> list[str]:
        try:
            response = self._request("GET", "/")
            payload = response.json()
            if isinstance(payload, dict):
                return list(payload.get("endpoints") or [])
        except HostedMcpError:
            return []
        return []

    def list_docs_capabilities(self) -> list[str]:
        """Docs-related endpoints exposed by the hosted server."""
        health = self.health_check()
        docs_endpoints = [
            endpoint
            for endpoint in health.endpoints
            if "doc" in endpoint.lower() or endpoint == "/"
        ]
        if self.config.append_to_doc_path in health.endpoints or any(
            "append" in endpoint for endpoint in health.endpoints
        ):
            return sorted(set(docs_endpoints + [self.config.append_to_doc_path]))
        return docs_endpoints

    def list_gmail_capabilities(self) -> list[str]:
        """Gmail-related endpoints exposed by the hosted server."""
        health = self.health_check()
        gmail_endpoints = [
            endpoint for endpoint in health.endpoints if "email" in endpoint.lower()
        ]
        if self.config.create_email_draft_path in health.endpoints or any(
            "draft" in endpoint for endpoint in health.endpoints
        ):
            return sorted(set(gmail_endpoints + [self.config.create_email_draft_path]))
        return gmail_endpoints

    def supports_send_email(self) -> bool:
        """True when hosted MCP advertises a send (not draft-only) endpoint."""
        health = self.health_check()
        send_path = self.config.send_email_path
        if send_path in health.endpoints:
            return True
        return any(
            "send" in endpoint.lower() and "email" in endpoint.lower()
            for endpoint in health.endpoints
        )

    def append_to_doc(self, *, doc_id: str, content: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            self.config.append_to_doc_path,
            json={"doc_id": doc_id, "content": content},
        )
        if not response.content:
            return {"status": "ok", "doc_id": doc_id}
        payload = response.json()
        if not isinstance(payload, dict):
            return {"status": "ok", "doc_id": doc_id, "raw": payload}
        return payload

    def create_email_draft(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            self.config.create_email_draft_path,
            json={"to": to, "subject": subject, "body": body},
        )
        if not response.content:
            return {"status": "ok", "to": to, "subject": subject}
        payload = response.json()
        if not isinstance(payload, dict):
            return {"status": "ok", "to": to, "subject": subject, "raw": payload}
        return payload

    def send_email(self, *, to: str, subject: str, body: str) -> dict[str, Any]:
        response = self._request(
            "POST",
            self.config.send_email_path,
            json={"to": to, "subject": subject, "body": body},
        )
        if not response.content:
            return {"status": "ok", "to": to, "subject": subject}
        payload = response.json()
        if not isinstance(payload, dict):
            return {"status": "ok", "to": to, "subject": subject, "raw": payload}
        return payload

    @staticmethod
    def get_document_url(document_id: str) -> str:
        return f"https://docs.google.com/document/d/{document_id}/edit"

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = self._client.request(method, path, **kwargs)
                if response.status_code >= 500 and attempt < MAX_RETRIES - 1:
                    delay = RETRY_BACKOFF_SECONDS * (2**attempt)
                    logger.warning(
                        "Hosted MCP %s %s returned %s; retry %s/%s in %.1fs",
                        method,
                        path,
                        response.status_code,
                        attempt + 1,
                        MAX_RETRIES,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                if response.status_code >= 400:
                    detail = response.text.strip() or response.reason_phrase
                    raise HostedMcpError(
                        f"{method} {path} failed ({response.status_code}): {detail}"
                    )
                return response
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= MAX_RETRIES - 1:
                    break
                delay = RETRY_BACKOFF_SECONDS * (2**attempt)
                logger.warning(
                    "Hosted MCP request error on %s %s: %s (retry %s/%s in %.1fs)",
                    method,
                    path,
                    exc,
                    attempt + 1,
                    MAX_RETRIES,
                    delay,
                )
                time.sleep(delay)
        raise HostedMcpError(f"Hosted MCP unreachable at {self.config.base_url}") from last_error
