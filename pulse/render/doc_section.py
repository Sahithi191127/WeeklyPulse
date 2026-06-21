"""Google Doc section builder — plain text for Docs MCP append."""

from __future__ import annotations

from datetime import datetime

from pulse.config import ProductConfig
from pulse.pipeline.models import PulseReport
from pulse.render.models import DocSection
from pulse.render.timezone_util import IST

WHO_THIS_HELPS: list[str] = [
    "Product — Prioritize roadmap from recurring themes in Play Store feedback",
    "Support — Spot repeating complaints and quality issues before they escalate",
    "Leadership — Fast health snapshot tied to verified customer voice",
]


def build_anchor(product: str, iso_week: str) -> str:
    return f"{product}-{iso_week}"


def build_heading_text(display_name: str, iso_week: str) -> str:
    return f"{display_name} — Weekly Review Pulse — {iso_week}"


def _format_generated_at(generated_at: datetime) -> str:
    local = generated_at.astimezone(IST)
    return local.strftime("%Y-%m-%d %H:%M IST")


def _metadata_line(*, window_weeks: int, generated_at: datetime) -> str:
    return (
        f"Period: Last {window_weeks} weeks (rolling) · "
        f"Source: Google Play Store · "
        f"Generated: {_format_generated_at(generated_at)}"
    )


def _bullet_lines(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items]


def _section(title: str, lines: list[str]) -> list[str]:
    block = [title, ""]
    block.extend(lines)
    return block


def build_doc_content(
    report: PulseReport,
    *,
    heading_text: str,
    window_weeks: int,
    generated_at: datetime,
) -> str:
    """Render the weekly section as plain text (no Docs formatting)."""
    themes = _bullet_lines([f"{t.theme_name} — {t.summary}" for t in report.themes])
    quotes: list[str] = []
    for theme in report.themes:
        quotes.extend(theme.quotes)
    quote_lines = _bullet_lines([f'"{quote}"' for quote in quotes])
    actions: list[str] = []
    for theme in report.themes:
        for action in theme.action_ideas:
            actions.append(f"{action.title} — {action.detail}")
    action_lines = _bullet_lines(actions)
    audience_lines = _bullet_lines(list(WHO_THIS_HELPS))

    lines: list[str] = [
        heading_text,
        "",
        _metadata_line(window_weeks=window_weeks, generated_at=generated_at),
        "",
    ]
    lines.extend(_section("Top themes", themes))
    lines.append("")
    lines.extend(_section("Real user quotes", quote_lines))
    lines.append("")
    lines.extend(_section("Action ideas", action_lines))
    lines.append("")
    lines.extend(_section("Who this helps", audience_lines))

    return "\n".join(lines).rstrip() + "\n"


def render_doc_section(
    report: PulseReport,
    *,
    product_config: ProductConfig,
    iso_week: str,
    generated_at: datetime | None = None,
) -> DocSection:
    """Build DocSection plain text from a PulseReport (no Google API calls)."""
    when = generated_at or datetime.now(tz=IST)
    anchor = build_anchor(product_config.product, iso_week)
    heading_text = build_heading_text(product_config.display_name, iso_week)
    content = build_doc_content(
        report,
        heading_text=heading_text,
        window_weeks=product_config.ingestion.window_weeks,
        generated_at=when,
    )
    return DocSection(anchor=anchor, heading_text=heading_text, content=content)
