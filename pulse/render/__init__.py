"""Report and email rendering — Phase 3."""

from pulse.render.doc_section import (
    build_anchor,
    build_doc_content,
    build_heading_text,
    render_doc_section,
)
from pulse.render.email_teaser import (
    DOC_DEEP_LINK_PLACEHOLDER,
    build_email_subject,
    build_idempotency_key,
    render_email_teaser,
)
from pulse.render.models import DocSection, EmailTeaser
from pulse.render.service import build_outputs, current_iso_week, resolve_default_iso_week

__all__ = [
    "DOC_DEEP_LINK_PLACEHOLDER",
    "DocSection",
    "EmailTeaser",
    "build_anchor",
    "build_doc_content",
    "build_email_subject",
    "build_heading_text",
    "build_idempotency_key",
    "build_outputs",
    "current_iso_week",
    "resolve_default_iso_week",
    "render_doc_section",
    "render_email_teaser",
]
