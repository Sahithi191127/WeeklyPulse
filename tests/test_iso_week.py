"""Tests for ISO week utilities (Phase 7)."""

from __future__ import annotations

from datetime import date

import pytest

from pulse.iso_week import (
    IsoWeekError,
    current_iso_week,
    iter_iso_weeks,
    parse_iso_week,
    resolve_default_iso_week,
)


def test_parse_iso_week_valid() -> None:
    assert parse_iso_week("2026-W23") == (2026, 23)


def test_parse_iso_week_invalid() -> None:
    with pytest.raises(IsoWeekError):
        parse_iso_week("2026-W99")


def test_iter_iso_weeks_inclusive() -> None:
    weeks = iter_iso_weeks("2026-W01", "2026-W03")
    assert weeks == ["2026-W01", "2026-W02", "2026-W03"]


def test_iter_iso_weeks_rejects_reverse_range() -> None:
    with pytest.raises(IsoWeekError):
        iter_iso_weeks("2026-W10", "2026-W05")


def test_resolve_default_iso_week_auto_monday_uses_previous(monkeypatch) -> None:
    monkeypatch.delenv("PULSE_ISO_WEEK_POLICY", raising=False)
    monday = date(2026, 6, 8)  # Monday, ISO week 2026-W24
    assert resolve_default_iso_week(on_date=monday, policy="auto") == "2026-W23"


def test_resolve_default_iso_week_auto_tuesday_uses_current(monkeypatch) -> None:
    monkeypatch.delenv("PULSE_ISO_WEEK_POLICY", raising=False)
    tuesday = date(2026, 6, 9)
    assert resolve_default_iso_week(on_date=tuesday, policy="auto") == "2026-W24"


def test_current_iso_week_matches_date() -> None:
    assert current_iso_week(on_date=date(2026, 6, 9)) == "2026-W24"
