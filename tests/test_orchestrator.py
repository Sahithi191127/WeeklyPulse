"""Integration tests for orchestrator (Phase 6)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from pulse.agent.orchestrator import RunOptions, run_pulse, save_run_artifacts
from pulse.config import HostedMcpConfig
from pulse.agent.mcp_client import HostedGoogleWorkspaceClient
from pulse.ingestion.models import IngestionResult, Review
from pulse.ledger.store import RunLedger
from pulse.pipeline.models import PulseReport
from pulse.render.models import DocSection, EmailTeaser
from pulse.agent import email_delivery as email_delivery_module
from pulse.agent import docs_delivery as docs_delivery_module
from pulse.agent import orchestrator as orchestrator_module


@pytest.fixture
def iso_week() -> str:
    return "2026-W99"


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


@pytest.fixture
def mock_mcp_client():
    calls: dict[str, int] = {"append": 0, "draft": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/append_to_doc":
            calls["append"] += 1
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/create_email_draft":
            calls["draft"] += 1
            return httpx.Response(200, json={"draft_id": "draft-123"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(base_url="https://example.test", transport=transport)
    config = HostedMcpConfig(base_url="https://example.test")
    client = HostedGoogleWorkspaceClient(config, http_client=http_client)
    client._test_calls = calls  # type: ignore[attr-defined]
    yield client
    client.close()


@pytest.fixture
def orchestrator_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    sample_report: PulseReport,
    sample_doc_section: DocSection,
    sample_email_teaser: EmailTeaser,
    iso_week: str,
):
    ledger = RunLedger(tmp_path / "ledger.sqlite")
    monkeypatch.setattr(docs_delivery_module, "DOCS_DELIVERY_DIR", tmp_path / "docs")
    monkeypatch.setattr(email_delivery_module, "EMAIL_DELIVERY_DIR", tmp_path / "email")
    monkeypatch.setattr(orchestrator_module, "RUNS_DIR", tmp_path / "runs")

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

    def fake_ingest(product: str, *, force_refresh: bool = False) -> IngestionResult:
        return IngestionResult(
            product=product,
            cache_dir=str(tmp_path / "cache"),
            raw_count=40,
            normalized_count=40,
            reviews=reviews,
            from_cache=True,
        )

    monkeypatch.setattr(orchestrator_module, "ingest_product", fake_ingest)

    def fake_run_pipeline(*args, **kwargs):
        return sample_report.model_copy(update={"stats": sample_report.stats.model_copy(update={"review_count": 40})})

    monkeypatch.setattr(orchestrator_module, "run_pipeline", fake_run_pipeline)

    def fake_build_outputs(report, *, product_config, iso_week, **kwargs):
        section = sample_doc_section.model_copy(update={"anchor": f"groww-{iso_week}"})
        teaser = sample_email_teaser.model_copy(
            update={"idempotency_key": f"groww-{iso_week}-email"}
        )
        return section, teaser

    monkeypatch.setattr(orchestrator_module, "build_outputs", fake_build_outputs)

    return ledger


def test_run_pulse_completes_with_mocked_mcp(
    orchestrator_env: RunLedger,
    mock_mcp_client,
    iso_week: str,
) -> None:
    outcome = run_pulse(
        RunOptions(product="groww", iso_week=iso_week, save_artifacts=True),
        ledger=orchestrator_env,
        mcp_client=mock_mcp_client,
    )

    assert outcome.status == "completed"
    assert outcome.skipped is False
    assert outcome.doc_delivery is not None
    assert outcome.email_delivery is not None
    assert outcome.review_count == 40

    completed = orchestrator_env.find_completed_run("groww", iso_week)
    assert completed is not None
    assert len(completed.deliveries) == 2


def test_run_pulse_idempotent_skip(
    orchestrator_env: RunLedger,
    mock_mcp_client,
    iso_week: str,
) -> None:
    run_pulse(
        RunOptions(product="groww", iso_week=iso_week),
        ledger=orchestrator_env,
        mcp_client=mock_mcp_client,
    )

    calls_before = mock_mcp_client._test_calls.copy()  # type: ignore[attr-defined]
    second = run_pulse(
        RunOptions(product="groww", iso_week=iso_week),
        ledger=orchestrator_env,
        mcp_client=mock_mcp_client,
    )

    assert second.skipped is True
    assert second.status == "skipped"
    calls_after = mock_mcp_client._test_calls  # type: ignore[attr-defined]
    assert calls_after["append"] == calls_before["append"]
    assert calls_after["draft"] == calls_before["draft"]


def test_partial_failure_retries_email_only(
    orchestrator_env: RunLedger,
    mock_mcp_client,
    sample_report: PulseReport,
    sample_doc_section: DocSection,
    sample_email_teaser: EmailTeaser,
    iso_week: str,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = f"groww-{iso_week}-partial"
    orchestrator_env.create_run(
        run_id=run_id,
        product="groww",
        iso_week=iso_week,
        email_mode="draft",
    )
    orchestrator_env.add_delivery(
        run_id,
        channel="google_doc",
        external_id="doc-test",
        url="https://docs.google.com/document/d/doc-test/edit",
        idempotency_key=f"groww-{iso_week}",
    )
    orchestrator_env.mark_failed(run_id, error_message="Gmail delivery failed")

    save_run_artifacts(
        run_id,
        report=sample_report,
        doc_section=sample_doc_section.model_copy(update={"anchor": f"groww-{iso_week}"}),
        email_teaser=sample_email_teaser.model_copy(
            update={"idempotency_key": f"groww-{iso_week}-email"}
        ),
    )

    calls_before = mock_mcp_client._test_calls.copy()  # type: ignore[attr-defined]
    outcome = run_pulse(
        RunOptions(product="groww", iso_week=iso_week),
        ledger=orchestrator_env,
        mcp_client=mock_mcp_client,
    )

    assert outcome.status == "completed"
    calls_after = mock_mcp_client._test_calls  # type: ignore[attr-defined]
    assert calls_after["append"] == calls_before["append"]
    assert calls_after["draft"] == calls_before["draft"] + 1

    completed = orchestrator_env.find_completed_run("groww", iso_week)
    assert completed is not None
    assert completed.run_id == run_id
