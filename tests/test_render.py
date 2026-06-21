"""Snapshot tests for Doc and email rendering."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from pulse.config import load_product_config
from pulse.pipeline.models import PulseReport
from pulse.render import (
    build_anchor,
    build_heading_text,
    build_idempotency_key,
    build_outputs,
    current_iso_week,
    render_doc_section,
    render_email_teaser,
)
from pulse.render.timezone_util import IST

FIXED_TIME = datetime(2026, 6, 8, 9, 30, tzinfo=IST)
ISO_WEEK = "2026-W24"


@pytest.fixture
def sample_report(fixtures_dir) -> PulseReport:
    raw = json.loads((fixtures_dir / "sample_pulse_report.json").read_text(encoding="utf-8"))
    return PulseReport.model_validate(raw)


@pytest.fixture
def groww_config():
    return load_product_config("groww")


def test_build_anchor_and_heading() -> None:
    assert build_anchor("groww", ISO_WEEK) == "groww-2026-W24"
    assert build_heading_text("Groww", ISO_WEEK) == "Groww — Weekly Review Pulse — 2026-W24"
    assert build_idempotency_key("groww", ISO_WEEK) == "groww-2026-W24-email"


def test_current_iso_week() -> None:
    assert current_iso_week(on_date=datetime(2026, 6, 8, tzinfo=IST).date()) == "2026-W24"


def test_render_doc_section_matches_fixture(sample_report, groww_config, fixtures_dir) -> None:
    doc_section = render_doc_section(
        sample_report,
        product_config=groww_config,
        iso_week=ISO_WEEK,
        generated_at=FIXED_TIME,
    )
    expected = json.loads((fixtures_dir / "expected_doc_section.json").read_text(encoding="utf-8"))
    assert json.loads(doc_section.model_dump_json()) == expected


def test_render_email_teaser_matches_fixture(sample_report, groww_config, fixtures_dir) -> None:
    email_teaser = render_email_teaser(
        sample_report,
        product_config=groww_config,
        iso_week=ISO_WEEK,
        generated_at=FIXED_TIME,
    )
    expected = json.loads((fixtures_dir / "expected_email_teaser.json").read_text(encoding="utf-8"))
    assert json.loads(email_teaser.model_dump_json()) == expected


def test_build_outputs_returns_stable_json(sample_report, groww_config) -> None:
    doc_section, email_teaser = build_outputs(
        sample_report,
        product_config=groww_config,
        iso_week=ISO_WEEK,
        generated_at=FIXED_TIME,
    )
    doc_json = json.loads(doc_section.model_dump_json())
    email_json = json.loads(email_teaser.model_dump_json())

    assert doc_json["anchor"] == "groww-2026-W24"
    assert doc_json["content"].startswith("Groww — Weekly Review Pulse — 2026-W24")
    assert "Top themes" in doc_json["content"]
    assert email_json["theme_bullets"]
    assert email_json["idempotency_key"] == "groww-2026-W24-email"
    assert "{{DOC_SECTION_URL}}" in email_json["cta_url"]


def test_email_teaser_uses_provided_deep_link(sample_report, groww_config) -> None:
    deep_link = "https://docs.google.com/document/d/abc123#heading=h.xyz"
    email_teaser = render_email_teaser(
        sample_report,
        product_config=groww_config,
        iso_week=ISO_WEEK,
        doc_deep_link=deep_link,
        generated_at=FIXED_TIME,
    )
    assert email_teaser.cta_url == deep_link
    assert deep_link in email_teaser.text_body
    assert deep_link in email_teaser.html_body


def test_render_doc_section_empty_themes(groww_config) -> None:
    from pulse.pipeline.models import PipelineStats

    report = PulseReport(
        product="groww",
        themes=[],
        stats=PipelineStats(
            review_count=25,
            noise_count=25,
            noise_pct=100.0,
            cluster_count=0,
        ),
    )
    doc_section = render_doc_section(
        report,
        product_config=groww_config,
        iso_week=ISO_WEEK,
        generated_at=FIXED_TIME,
    )
    assert "Top themes\n\n\nReal user quotes" in doc_section.content
