"""Orchestrator and hosted MCP client."""

from pulse.agent.docs_delivery import (
    DocsDeliveryError,
    append_section,
    deliver_doc_section,
    find_section_by_anchor,
)
from pulse.agent.email_delivery import (
    EmailDeliveryError,
    apply_doc_url_to_teaser,
    check_idempotency,
    create_email_draft,
    deliver_email_teaser,
    format_recipients,
    resolve_doc_url,
    send_email,
)
from pulse.agent.mcp_client import HostedGoogleWorkspaceClient, HostedMcpError
from pulse.agent.models import (
    AppendSectionResult,
    CreateEmailDraftResult,
    DocDeliveryResult,
    EmailDeliveryResult,
    EmailIdempotencyResult,
    FindSectionResult,
    McpHealthStatus,
    SendEmailResult,
)
from pulse.agent.orchestrator import (
    OrchestratorError,
    RunOptions,
    generate_run_id,
    load_run_artifacts,
    run_pulse,
    save_run_artifacts,
)
from pulse.config import resolve_email_mode

__all__ = [
    "AppendSectionResult",
    "CreateEmailDraftResult",
    "DocDeliveryResult",
    "DocsDeliveryError",
    "EmailDeliveryError",
    "EmailDeliveryResult",
    "EmailIdempotencyResult",
    "FindSectionResult",
    "HostedGoogleWorkspaceClient",
    "HostedMcpError",
    "McpHealthStatus",
    "OrchestratorError",
    "RunOptions",
    "SendEmailResult",
    "append_section",
    "apply_doc_url_to_teaser",
    "check_idempotency",
    "create_email_draft",
    "deliver_doc_section",
    "deliver_email_teaser",
    "find_section_by_anchor",
    "format_recipients",
    "generate_run_id",
    "load_run_artifacts",
    "resolve_doc_url",
    "resolve_email_mode",
    "run_pulse",
    "save_run_artifacts",
    "send_email",
]
