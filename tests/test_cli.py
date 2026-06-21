"""CLI command tests (Phase 7)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pulse.cli import app
from pulse.ledger.models import RunOutcome
from pulse.ledger.store import RunLedger


runner = CliRunner()


@pytest.fixture
def ledger(tmp_path) -> RunLedger:
    return RunLedger(tmp_path / "ledger.sqlite")


def test_status_not_found(monkeypatch: pytest.MonkeyPatch, ledger: RunLedger) -> None:
    monkeypatch.setattr("pulse.cli.RunLedger", lambda: ledger)
    result = runner.invoke(app, ["status", "--product", "groww", "--iso-week", "2026-W01"])
    assert result.exit_code == 1
    assert "No run found" in result.output


def test_status_shows_completed_run(monkeypatch: pytest.MonkeyPatch, ledger: RunLedger) -> None:
    started = datetime(2026, 6, 8, 4, 0, tzinfo=timezone.utc)
    ledger.create_run(
        run_id="groww-2026-W23-abc123",
        product="groww",
        iso_week="2026-W23",
        email_mode="draft",
        started_at=started,
    )
    ledger.add_delivery(
        "groww-2026-W23-abc123",
        channel="google_doc",
        external_id="doc-1",
        url="https://docs.google.com/document/d/doc-1/edit",
        idempotency_key="groww-2026-W23",
    )
    ledger.add_delivery(
        "groww-2026-W23-abc123",
        channel="gmail",
        external_id="draft-99",
        idempotency_key="groww-2026-W23-email",
    )
    ledger.mark_completed("groww-2026-W23-abc123", review_count=100, window_weeks=10)

    monkeypatch.setattr("pulse.cli.RunLedger", lambda: ledger)
    result = runner.invoke(app, ["status", "--product", "groww", "--iso-week", "2026-W23"])
    assert result.exit_code == 0
    assert "groww-2026-W23-abc123" in result.output
    assert "completed" in result.output
    assert "groww-2026-W23-email" in result.output


def test_status_json(monkeypatch: pytest.MonkeyPatch, ledger: RunLedger) -> None:
    ledger.create_run(
        run_id="groww-2026-W24-x",
        product="groww",
        iso_week="2026-W24",
    )
    ledger.mark_failed("groww-2026-W24-x", error_message="Gmail delivery failed")

    monkeypatch.setattr("pulse.cli.RunLedger", lambda: ledger)
    result = runner.invoke(
        app,
        ["status", "--product", "groww", "--iso-week", "2026-W24", "--json"],
    )
    assert result.exit_code == 0
    assert '"status": "failed"' in result.output
    assert "Gmail delivery failed" in result.output


def test_dry_run_invokes_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_run_pulse(options, **kwargs):
        captured["options"] = options
        return RunOutcome(
            run_id="groww-2026-W24-dry",
            product=options.product,
            iso_week=options.iso_week or "2026-W24",
            status="completed",
            review_count=50,
            window_weeks=10,
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            artifact_dir="/tmp/runs/test",
        )

    monkeypatch.setattr("pulse.cli.run_pulse", fake_run_pulse)
    monkeypatch.setattr("pulse.cli.validate_all_configs", lambda *a, **k: None)
    monkeypatch.setattr("pulse.cli.load_product_config", lambda p: type("PC", (), {"product": p})())

    result = runner.invoke(
        app,
        ["dry-run", "--product", "groww", "--iso-week", "2026-W24"],
    )
    assert result.exit_code == 0
    assert captured["options"].dry_run is True
    assert captured["options"].iso_week == "2026-W24"
    assert "Dry-run OK" in result.output


def test_backfill_skips_completed_weeks(
    monkeypatch: pytest.MonkeyPatch,
    ledger: RunLedger,
) -> None:
    ledger.create_run(run_id="groww-2026-W20-done", product="groww", iso_week="2026-W20")
    ledger.mark_completed("groww-2026-W20-done", review_count=10, window_weeks=10)

    calls: list[str] = []

    def fake_run_pulse(options, **kwargs):
        calls.append(options.iso_week or "")
        return RunOutcome(
            run_id=f"groww-{options.iso_week}-new",
            product=options.product,
            iso_week=options.iso_week or "",
            status="completed",
            started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr("pulse.cli.run_pulse", fake_run_pulse)
    monkeypatch.setattr("pulse.cli.RunLedger", lambda: ledger)
    monkeypatch.setattr("pulse.cli.validate_all_configs", lambda *a, **k: None)
    monkeypatch.setattr("pulse.cli.validate_delivery_config", lambda *a, **k: None)
    monkeypatch.setattr("pulse.cli.load_product_config", lambda p: type("PC", (), {"product": p})())

    result = runner.invoke(
        app,
        ["backfill", "--product", "groww", "--from", "2026-W20", "--to", "2026-W21"],
    )
    assert result.exit_code == 0
    assert "Skipping 2026-W20" in result.output
    assert calls == ["2026-W21"]


def test_run_invalid_email_mode() -> None:
    result = runner.invoke(
        app,
        ["run", "--product", "groww", "--iso-week", "2026-W24", "--email-mode", "invalid"],
    )
    assert result.exit_code == 1
    assert "Invalid --email-mode" in result.output


def test_quality_gate_passes_completed_run(
    monkeypatch: pytest.MonkeyPatch,
    ledger: RunLedger,
    tmp_path: Path,
    fixtures_dir: Path,
) -> None:
    from pulse.agent import orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "RUNS_DIR", tmp_path / "runs")
    run_id = "groww-2026-W23-gate"
    ledger.create_run(run_id=run_id, product="groww", iso_week="2026-W23")
    ledger.add_delivery(
        run_id,
        channel="google_doc",
        external_id="doc-1",
        url="https://docs.google.com/document/d/doc-1/edit",
        idempotency_key="groww-2026-W23",
    )
    ledger.add_delivery(
        run_id,
        channel="gmail",
        external_id="draft-1",
        idempotency_key="groww-2026-W23-email",
    )
    ledger.mark_completed(run_id, review_count=100, window_weeks=10)

    doc_url = "https://docs.google.com/document/d/doc-1/edit"
    runs_dir = tmp_path / "runs" / run_id
    runs_dir.mkdir(parents=True)
    doc = json.loads((fixtures_dir / "expected_doc_section.json").read_text(encoding="utf-8"))
    email = json.loads((fixtures_dir / "expected_email_teaser.json").read_text(encoding="utf-8"))
    email["cta_url"] = doc_url
    email["text_body"] = email["text_body"].replace("{{DOC_SECTION_URL}}", doc_url)
    email["html_body"] = email["html_body"].replace("{{DOC_SECTION_URL}}", doc_url)
    (runs_dir / "pulse_report.json").write_text(
        (fixtures_dir / "sample_pulse_report.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (runs_dir / "doc_section.json").write_text(json.dumps(doc), encoding="utf-8")
    (runs_dir / "email_teaser.json").write_text(json.dumps(email), encoding="utf-8")

    monkeypatch.setattr("pulse.cli.RunLedger", lambda: ledger)
    monkeypatch.setattr("pulse.quality_gate.RunLedger", lambda: ledger)

    result = runner.invoke(
        app,
        ["quality-gate", "--product", "groww", "--iso-week", "2026-W23"],
    )
    assert result.exit_code == 0
    assert "PASSED" in result.output
