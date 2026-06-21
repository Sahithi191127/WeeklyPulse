"""ISO week parsing, iteration, and default week policy (Phase 7)."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta

from pulse.timezone_util import IST

_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


class IsoWeekError(ValueError):
    """Raised when an ISO week string is invalid."""


def parse_iso_week(value: str) -> tuple[int, int]:
    """Parse `YYYY-Www` into `(iso_year, iso_week)`."""
    match = _ISO_WEEK_RE.fullmatch(value.strip())
    if not match:
        raise IsoWeekError(f"Invalid ISO week: {value!r} (expected YYYY-Www)")
    year = int(match.group(1))
    week = int(match.group(2))
    if week < 1 or week > 53:
        raise IsoWeekError(f"Invalid ISO week number in {value!r}")
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise IsoWeekError(f"Invalid ISO week: {value!r}") from exc
    return year, week


def format_iso_week(year: int, week: int) -> str:
    return f"{year}-W{week:02d}"


def iso_week_from_date(on_date: date) -> str:
    year, week, _ = on_date.isocalendar()
    return format_iso_week(year, week)


def current_iso_week(*, on_date: date | None = None) -> str:
    """ISO week for a calendar date (defaults to today in IST)."""
    target = on_date or datetime.now(tz=IST).date()
    return iso_week_from_date(target)


def resolve_default_iso_week(*, on_date: date | None = None, policy: str | None = None) -> str:
    """
    Default ISO week for scheduled / implicit runs.

    Policy (env `PULSE_ISO_WEEK_POLICY`, default `auto`):
    - `auto` — previous ISO week on Monday IST, else current week
    - `current` — always the week containing the run date (IST)
    - `previous` — always the prior ISO week
    """
    target = on_date or datetime.now(tz=IST).date()
    chosen = (policy or os.environ.get("PULSE_ISO_WEEK_POLICY", "auto")).strip().lower()

    if chosen == "current":
        return iso_week_from_date(target)
    if chosen == "previous":
        return iso_week_from_date(target - timedelta(weeks=1))
    if chosen != "auto":
        raise IsoWeekError(
            f"Unknown PULSE_ISO_WEEK_POLICY={chosen!r} (use auto, current, or previous)"
        )

    if target.weekday() == 0:
        return iso_week_from_date(target - timedelta(weeks=1))
    return iso_week_from_date(target)


def iter_iso_weeks(from_week: str, to_week: str) -> list[str]:
    """Inclusive range of ISO weeks from `from_week` through `to_week`."""
    start = date.fromisocalendar(*parse_iso_week(from_week), 1)
    end = date.fromisocalendar(*parse_iso_week(to_week), 1)
    if start > end:
        raise IsoWeekError(f"Start week {from_week} is after end week {to_week}")

    weeks: list[str] = []
    current = start
    while current <= end:
        weeks.append(iso_week_from_date(current))
        current += timedelta(weeks=1)
    return weeks
