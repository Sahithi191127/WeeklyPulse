"""Combine Doc and email rendering for orchestrator / dry-run."""

from __future__ import annotations

from datetime import datetime

from pulse.config import ProductConfig
from pulse.iso_week import current_iso_week, resolve_default_iso_week
from pulse.pipeline.models import PulseReport
from pulse.render.doc_section import render_doc_section
from pulse.render.email_teaser import render_email_teaser
from pulse.render.models import DocSection, EmailTeaser
from pulse.timezone_util import IST


def build_outputs(
    report: PulseReport,
    *,
    product_config: ProductConfig,
    iso_week: str | None = None,
    doc_deep_link: str | None = None,
    generated_at: datetime | None = None,
) -> tuple[DocSection, EmailTeaser]:
    """Render DocSection and EmailTeaser from a PulseReport."""
    week = iso_week or current_iso_week()
    doc_section = render_doc_section(
        report,
        product_config=product_config,
        iso_week=week,
        generated_at=generated_at,
    )
    email_teaser = render_email_teaser(
        report,
        product_config=product_config,
        iso_week=week,
        doc_deep_link=doc_deep_link,
        generated_at=generated_at,
    )
    return doc_section, email_teaser
