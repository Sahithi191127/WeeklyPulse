"""SQLite run ledger — Phase 6."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pulse.config import REPO_ROOT
from pulse.ledger.models import DeliveryChannel, DeliveryRecord, RunRecord, RunStatus

DEFAULT_LEDGER_PATH = REPO_ROOT / "data" / "ledger.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    product TEXT NOT NULL,
    iso_week TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
    review_count INTEGER,
    window_weeks INTEGER,
    email_mode TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error_message TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_completed_product_week
ON runs(product, iso_week)
WHERE status = 'completed';

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    channel TEXT NOT NULL CHECK(channel IN ('google_doc', 'gmail')),
    external_id TEXT,
    url TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_deliveries_run_id ON deliveries(run_id);
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_str(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _str_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


class RunLedger:
    """SQLite-backed run ledger with idempotency for completed runs."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_LEDGER_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def find_completed_run(self, product: str, iso_week: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM runs
                WHERE product = ? AND iso_week = ? AND status = 'completed'
                ORDER BY completed_at DESC
                LIMIT 1
                """,
                (product, iso_week),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row, include_deliveries=True)

    def find_latest_run(self, product: str, iso_week: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM runs
                WHERE product = ? AND iso_week = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (product, iso_week),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row, include_deliveries=True)

    def find_failed_run_with_doc_delivery(
        self, product: str, iso_week: str
    ) -> RunRecord | None:
        run = self.find_latest_run(product, iso_week)
        if run is None or run.status != "failed":
            return None
        if any(delivery.channel == "google_doc" for delivery in run.deliveries):
            return run
        return None

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row, include_deliveries=True)

    def create_run(
        self,
        *,
        run_id: str,
        product: str,
        iso_week: str,
        email_mode: str | None = None,
        started_at: datetime | None = None,
    ) -> RunRecord:
        started = started_at or _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, product, iso_week, status, email_mode, started_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (run_id, product, iso_week, email_mode, _dt_to_str(started)),
            )
        return RunRecord(
            run_id=run_id,
            product=product,
            iso_week=iso_week,
            status="pending",
            email_mode=email_mode,  # type: ignore[arg-type]
            started_at=started,
        )

    def mark_completed(
        self,
        run_id: str,
        *,
        review_count: int,
        window_weeks: int,
        completed_at: datetime | None = None,
    ) -> None:
        finished = completed_at or _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = 'completed',
                    review_count = ?,
                    window_weeks = ?,
                    completed_at = ?,
                    error_message = NULL
                WHERE run_id = ?
                """,
                (review_count, window_weeks, _dt_to_str(finished), run_id),
            )

    def mark_failed(
        self,
        run_id: str,
        *,
        error_message: str,
        completed_at: datetime | None = None,
        review_count: int | None = None,
        window_weeks: int | None = None,
    ) -> None:
        finished = completed_at or _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = 'failed',
                    error_message = ?,
                    completed_at = ?,
                    review_count = COALESCE(?, review_count),
                    window_weeks = COALESCE(?, window_weeks)
                WHERE run_id = ?
                """,
                (
                    error_message,
                    _dt_to_str(finished),
                    review_count,
                    window_weeks,
                    run_id,
                ),
            )

    def add_delivery(
        self,
        run_id: str,
        *,
        channel: DeliveryChannel,
        external_id: str | None = None,
        url: str | None = None,
        idempotency_key: str | None = None,
        created_at: datetime | None = None,
    ) -> DeliveryRecord:
        created = created_at or _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO deliveries (
                    run_id, channel, external_id, url, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    channel,
                    external_id,
                    url,
                    idempotency_key,
                    _dt_to_str(created),
                ),
            )
        return DeliveryRecord(
            channel=channel,
            external_id=external_id,
            url=url,
            idempotency_key=idempotency_key,
            created_at=created,
        )

    def _row_to_run(self, row: sqlite3.Row, *, include_deliveries: bool) -> RunRecord:
        deliveries: list[DeliveryRecord] = []
        if include_deliveries:
            deliveries = self._load_deliveries(row["run_id"])
        return RunRecord(
            run_id=row["run_id"],
            product=row["product"],
            iso_week=row["iso_week"],
            status=row["status"],  # type: ignore[arg-type]
            review_count=row["review_count"],
            window_weeks=row["window_weeks"],
            email_mode=row["email_mode"],  # type: ignore[arg-type]
            started_at=_str_to_dt(row["started_at"]) or _utc_now(),
            completed_at=_str_to_dt(row["completed_at"]),
            error_message=row["error_message"],
            deliveries=deliveries,
        )

    def _load_deliveries(self, run_id: str) -> list[DeliveryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT channel, external_id, url, idempotency_key, created_at
                FROM deliveries
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            DeliveryRecord(
                channel=row["channel"],  # type: ignore[arg-type]
                external_id=row["external_id"],
                url=row["url"],
                idempotency_key=row["idempotency_key"],
                created_at=_str_to_dt(row["created_at"]),
            )
            for row in rows
        ]
