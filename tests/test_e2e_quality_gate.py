"""Phase 8 quality-gate and E2E integration tests."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from pulse.agent.orchestrator import RunOptions, run_pulse, save_run_artifacts
from pulse.config import HostedMcpConfig, load_product_config
from pulse.agent.mcp_client import HostedGoogleWorkspaceClient
from pulse.ingestion.models import IngestionResult, Review
from pulse.ledger.store import RunLedger
from pulse.pipeline.models import PulseReport
from pulse.pipeline.scrubber import scrub_text
from pulse.quality_gate import (
    QualityGateError,
    QualityGateResult,
    validate_completed_run,
    validate_doc_section,
    validate_email_teaser,
    validate_outcome,
    validate_run_artifacts,
)
from pulse.render import build_outputs
from pulse.render.models import DocSection, EmailTeaser
from pulse.agent import email_delivery as email_delivery_module
from pulse.agent import docs_delivery as docs_delivery_module
from pulse.agent import orchestrator as orchestrator_module


@pytest.fixture
def sample_report(fixtures_dir: Path) -> PulseReport:
    return PulseReport.model_validate_json(
        (fixtures_dir / "sample_pulse_report.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def sample_doc_section(fixtures_dir: Path) -> DocSection:
    return DocSection.model_validate_json(
        (fixtures_dir / "expected_doc_section.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def sample_email_teaser(fixtures_dir: Path) -> EmailTeaser:
    return EmailTeaser.model_validate_json(
        (fixtures_dir / "expected_email_teaser.json").read_text(encoding="utf-8")
    )


def test_doc_section_structure_passes_fixture(sample_doc_section: DocSection) -> None:
    errors = validate_doc_section(
        sample_doc_section,
        product="groww",
        iso_week="2026-W24",
    )
    assert errors == []


def test_email_teaser_structure_passes_fixture(
    sample_email_teaser: EmailTeaser,
) -> None:
    doc_url = "https://docs.google.com/document/d/abc123/edit"
    teaser = sample_email_teaser.model_copy(
        update={
            "cta_url": doc_url,
            "text_body": sample_email_teaser.text_body.replace("{{DOC_SECTION_URL}}", doc_url),
            "html_body": sample_email_teaser.html_body.replace("{{DOC_SECTION_URL}}", doc_url),
        }
    )
    errors = validate_email_teaser(
        teaser,
        product="groww",
        iso_week="2026-W24",
        doc_url=doc_url,
    )
    assert errors == []


def test_pii_scrubbed_before_render_output(sample_report: PulseReport) -> None:
    """PII in source reviews must not appear in Doc/email payloads."""
    pii_text = (
        "Contact me at user@example.com or +919876543210 about brokerage charges "
        "and trading lag during market hours with detailed frustration"
    )
    scrubbed = scrub_text(pii_text)
    assert "[EMAIL]" in scrubbed
    assert "[PHONE]" in scrubbed
    assert "user@example.com" not in scrubbed

    groww_config = load_product_config("groww")
    report = sample_report.model_copy(
        update={
            "themes": [
                sample_report.themes[0].model_copy(
                    update={
                        "quotes": [scrubbed],
                        "summary": "Users report contact and trading issues.",
                    }
                )
            ]
        }
    )
    doc_section, email_teaser = build_outputs(
        report,
        product_config=groww_config,
        iso_week="2026-W24",
    )
    assert "user@example.com" not in doc_section.content
    assert "+919876543210" not in doc_section.content
    assert "user@example.com" not in email_teaser.text_body


def test_validate_run_artifacts_from_disk(
    sample_report: PulseReport,
    sample_doc_section: DocSection,
    sample_email_teaser: EmailTeaser,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "groww-2026-W90-test"
    doc_url = "https://docs.google.com/document/d/doc-1/edit"
    monkeypatch.setattr(orchestrator_module, "RUNS_DIR", tmp_path / "runs")
    save_run_artifacts(
        run_id,
        report=sample_report,
        doc_section=sample_doc_section,
        email_teaser=sample_email_teaser.model_copy(
            update={
                "cta_url": doc_url,
                "text_body": sample_email_teaser.text_body.replace(
                    "{{DOC_SECTION_URL}}", doc_url
                ),
                "html_body": sample_email_teaser.html_body.replace(
                    "{{DOC_SECTION_URL}}", doc_url
                ),
            }
        ),
    )
    artifact_dir = tmp_path / "runs" / run_id
    result = validate_run_artifacts(
        artifact_dir,
        product="groww",
        iso_week="2026-W24",
        doc_url=doc_url,
    )
    assert result.passed, result.errors
    assert result.metrics["review_count"] == sample_report.stats.review_count
    assert result.metrics["theme_count"] == len(sample_report.themes)


@pytest.fixture
def backfill_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    sample_report: PulseReport,
    sample_doc_section: DocSection,
    sample_email_teaser: EmailTeaser,
):
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    monkeypatch.setattr(docs_delivery_module, "DOCS_DELIVERY_DIR", tmp_path / "docs")
    monkeypatch.setattr(email_delivery_module, "EMAIL_DELIVERY_DIR", tmp_path / "email")
    runs_dir = tmp_path / "runs"
    monkeypatch.setattr(orchestrator_module, "RUNS_DIR", runs_dir)

    reviews = [
        Review(
            text=(
                f"Review {i} mentions brokerage charges and trading lag during market hours "
                f"with detailed frustration about the mobile application experience"
            ),
            rating=1 if i % 2 == 0 else 2,
        )
        for i in range(40)
    ]

    monkeypatch.setattr(
        orchestrator_module,
        "ingest_product",
        lambda product, **kw: IngestionResult(
            product=product,
            cache_dir=str(tmp_path / "cache"),
            raw_count=40,
            normalized_count=40,
            reviews=reviews,
            from_cache=True,
        ),
    )
    monkeypatch.setattr(
        orchestrator_module,
        "run_pipeline",
        lambda *a, **k: sample_report.model_copy(
            update={"stats": sample_report.stats.model_copy(update={"review_count": 40})}
        ),
    )

    def fake_build_outputs(report, *, product_config, iso_week, **kwargs):
        from pulse.agent.mcp_client import HostedGoogleWorkspaceClient

        section = sample_doc_section.model_copy(update={"anchor": f"groww-{iso_week}"})
        doc_url = HostedGoogleWorkspaceClient.get_document_url(
            product_config.delivery.google_doc_id
        )
        teaser = sample_email_teaser.model_copy(
            update={
                "idempotency_key": f"groww-{iso_week}-email",
                "cta_url": doc_url,
                "text_body": sample_email_teaser.text_body.replace(
                    "{{DOC_SECTION_URL}}", doc_url
                ),
                "html_body": sample_email_teaser.html_body.replace(
                    "{{DOC_SECTION_URL}}", doc_url
                ),
            }
        )
        return section, teaser

    monkeypatch.setattr(orchestrator_module, "build_outputs", fake_build_outputs)
    return ledger


@pytest.fixture
def mock_mcp_client():
    calls: dict[str, int] = {"append": 0, "draft": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/append_to_doc":
            calls["append"] += 1
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/create_email_draft":
            calls["draft"] += 1
            return httpx.Response(200, json={"draft_id": f"draft-{calls['draft']}"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://example.test", transport=transport)
    config = HostedMcpConfig(base_url="https://example.test")
    client = HostedGoogleWorkspaceClient(config, http_client=http_client)
    client._test_calls = calls  # type: ignore[attr-defined]
    yield client
    client.close()


def test_backfill_three_weeks_idempotent(
    backfill_env: RunLedger,
    mock_mcp_client,
) -> None:
    """Load test: 3-week backfill then re-run — no duplicate MCP writes."""
    ledger = backfill_env
    weeks = ["2026-W91", "2026-W92", "2026-W93"]

    for week in weeks:
        outcome = run_pulse(
            RunOptions(product="groww", iso_week=week, save_artifacts=True),
            ledger=ledger,
            mcp_client=mock_mcp_client,
        )
        gate = validate_outcome(outcome, product="groww", iso_week=week)
        assert gate.passed, gate.errors

    assert mock_mcp_client._test_calls["append"] == 3  # type: ignore[attr-defined]
    assert mock_mcp_client._test_calls["draft"] == 3  # type: ignore[attr-defined]

    for week in weeks:
        rerun = run_pulse(
            RunOptions(product="groww", iso_week=week),
            ledger=ledger,
            mcp_client=mock_mcp_client,
        )
        assert rerun.skipped is True
        status = validate_completed_run(product="groww", iso_week=week, ledger=ledger)
        assert status.passed, status.errors

    assert mock_mcp_client._test_calls["append"] == 3  # type: ignore[attr-defined]
    assert mock_mcp_client._test_calls["draft"] == 3  # type: ignore[attr-defined]


def test_quality_gate_raises_on_invalid_doc(sample_doc_section: DocSection) -> None:
    bad = sample_doc_section.model_copy(update={"anchor": "wrong-anchor"})
    errors = validate_doc_section(bad, product="groww", iso_week="2026-W24")
    assert errors
    with pytest.raises(QualityGateError):
        QualityGateResult(passed=False, errors=errors).raise_if_failed()


@pytest.mark.staging
@pytest.mark.skipif(
    os.environ.get("STAGING_E2E") != "1",
    reason="Set STAGING_E2E=1 with real GROQ/MCP credentials for live staging run",
)
def test_staging_live_weekly_run() -> None:
    """
    Manual staging E2E — one real weekly run (draft email + Doc append).

    Run:
      STAGING_E2E=1 pytest tests/test_e2e_quality_gate.py::test_staging_live_weekly_run -v
    """
    from pulse.iso_week import resolve_default_iso_week

    iso_week = os.environ.get("PULSE_STAGING_ISO_WEEK") or resolve_default_iso_week()
    outcome = run_pulse(
        RunOptions(product="groww", iso_week=iso_week, email_mode="draft")
    )
    gate = validate_outcome(outcome, product="groww", iso_week=iso_week)
    gate.raise_if_failed()
    status = validate_completed_run(product="groww", iso_week=iso_week)
    status.raise_if_failed()
