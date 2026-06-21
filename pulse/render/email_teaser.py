"""Email teaser builder — HTML/text payloads for Gmail MCP."""

from __future__ import annotations

import html
from datetime import datetime

from pulse.config import ProductConfig
from pulse.pipeline.models import PulseReport
from pulse.render.models import EmailTeaser
from pulse.render.timezone_util import IST
DOC_DEEP_LINK_PLACEHOLDER = "{{DOC_SECTION_URL}}"


def build_email_subject(display_name: str, iso_week: str) -> str:
    return f"{display_name} Weekly Review Pulse — {iso_week}"


def build_idempotency_key(product: str, iso_week: str) -> str:
    return f"{product}-{iso_week}-email"


def _format_footer(
    *,
    window_weeks: int,
    generated_at: datetime,
) -> str:
    local = generated_at.astimezone(IST)
    timestamp = local.strftime("%Y-%m-%d %H:%M IST")
    return f"Review window: last {window_weeks} weeks (rolling) · Generated {timestamp}"


def _theme_bullets(report: PulseReport, *, max_bullets: int = 5) -> list[str]:
    bullets = [f"{theme.theme_name} — {theme.summary}" for theme in report.themes]
    return bullets[:max_bullets]


def _build_text_body(
    *,
    theme_bullets: list[str],
    cta_label: str,
    cta_url: str,
    footer: str,
) -> str:
    lines = ["Top themes this week:", ""]
    lines.extend(f"• {bullet}" for bullet in theme_bullets)
    lines.extend(["", f"{cta_label}: {cta_url}", "", footer])
    return "\n".join(lines)


def _build_html_body(
    *,
    theme_bullets: list[str],
    cta_label: str,
    cta_url: str,
    footer: str,
) -> str:
    items = "".join(f"<li>{html.escape(bullet)}</li>" for bullet in theme_bullets)
    safe_url = html.escape(cta_url, quote=True)
    safe_label = html.escape(cta_label)
    safe_footer = html.escape(footer)
    return (
        "<p>Top themes this week:</p>"
        f"<ul>{items}</ul>"
        f'<p><a href="{safe_url}">{safe_label}</a></p>'
        f"<p><small>{safe_footer}</small></p>"
    )


def render_email_teaser(
    report: PulseReport,
    *,
    product_config: ProductConfig,
    iso_week: str,
    doc_deep_link: str | None = None,
    generated_at: datetime | None = None,
) -> EmailTeaser:
    """Build EmailTeaser from a PulseReport (no Gmail API calls)."""
    when = generated_at or datetime.now(tz=IST)
    cta_url = doc_deep_link or DOC_DEEP_LINK_PLACEHOLDER
    theme_bullets = _theme_bullets(report)
    if not theme_bullets:
        theme_bullets = ["No themes identified this week — see the full report for details."]

    footer = _format_footer(
        window_weeks=product_config.ingestion.window_weeks,
        generated_at=when,
    )
    cta_label = "Read full report"

    return EmailTeaser(
        subject=build_email_subject(product_config.display_name, iso_week),
        theme_bullets=theme_bullets,
        cta_label=cta_label,
        cta_url=cta_url,
        text_body=_build_text_body(
            theme_bullets=theme_bullets,
            cta_label=cta_label,
            cta_url=cta_url,
            footer=footer,
        ),
        html_body=_build_html_body(
            theme_bullets=theme_bullets,
            cta_label=cta_label,
            cta_url=cta_url,
            footer=footer,
        ),
        footer=footer,
        idempotency_key=build_idempotency_key(product_config.product, iso_week),
    )
