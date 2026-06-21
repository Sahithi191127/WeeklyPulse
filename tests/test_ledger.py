"""Tests for SQLite run ledger (Phase 6)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pulse.ledger.store import RunLedger


@pytest.fixture
def ledger(tmp_path) -> RunLedger:
    return RunLedger(tmp_path / "ledger.sqlite")


def test_create_and_complete_run(ledger: RunLedger) -> None:
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
    ledger.mark_completed(
        "groww-2026-W23-abc123",
        review_count=872,
        window_weeks=10,
    )

    run = ledger.find_completed_run("groww", "2026-W23")
    assert run is not None
    assert run.status == "completed"
    assert run.review_count == 872
    assert len(run.deliveries) == 1
    assert run.deliveries[0].channel == "google_doc"


def test_completed_run_idempotency_unique(ledger: RunLedger) -> None:
    ledger.create_run(
        run_id="groww-2026-W24-a",
        product="groww",
        iso_week="2026-W24",
    )
    ledger.mark_completed("groww-2026-W24-a", review_count=100, window_weeks=10)

    ledger.create_run(
        run_id="groww-2026-W24-b",
        product="groww",
        iso_week="2026-W24",
    )
    with pytest.raises(Exception):
        ledger.mark_completed("groww-2026-W24-b", review_count=100, window_weeks=10)


def test_failed_run_with_partial_doc_delivery(ledger: RunLedger) -> None:
    ledger.create_run(
        run_id="groww-2026-W25-partial",
        product="groww",
        iso_week="2026-W25",
    )
    ledger.add_delivery(
        "groww-2026-W25-partial",
        channel="google_doc",
        external_id="doc-1",
        url="https://docs.google.com/document/d/doc-1/edit",
        idempotency_key="groww-2026-W25",
    )
    ledger.mark_failed("groww-2026-W25-partial", error_message="Gmail delivery failed")

    partial = ledger.find_failed_run_with_doc_delivery("groww", "2026-W25")
    assert partial is not None
    assert partial.status == "failed"
    assert any(d.channel == "google_doc" for d in partial.deliveries)
