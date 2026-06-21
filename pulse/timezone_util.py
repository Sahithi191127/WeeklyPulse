"""Timezone helpers (IST has no DST)."""

from __future__ import annotations

from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
