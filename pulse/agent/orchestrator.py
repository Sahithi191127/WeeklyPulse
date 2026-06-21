"""End-to-end run coordinator — Phase 6."""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pulse.agent.docs_delivery import deliver_doc_section
from pulse.agent.email_delivery import apply_doc_url_to_teaser, deliver_email_teaser
from pulse.agent.mcp_client import HostedGoogleWorkspaceClient, HostedMcpError, HostedMcpTransport
from pulse.config import (
    REPO_ROOT,
    ProductConfig,
    get_email_recipients,
    load_hosted_mcp_config,
    load_pipeline_config,
    load_product_config,
    resolve_email_mode,
)
from pulse.ingestion import IngestionError, ingest_product
from pulse.ledger.models import (
    DocDeliveryAudit,
    EmailDeliveryAudit,
    EmailMode,
    RunOutcome,
    RunRecord,
)
from pulse.ledger.store import RunLedger
from pulse.pipeline import PipelineError, run_pipeline
from pulse.iso_week import resolve_default_iso_week
from pulse.pipeline.models import PulseReport
from pulse.pipeline.service import save_report_artifact
from pulse.render import build_outputs
from pulse.render.models import DocSection, EmailTeaser

logger = logging.getLogger(__name__)

RUNS_DIR = REPO_ROOT / "data" / "runs"


class OrchestratorError(Exception):
    """Raised when the weekly pulse run cannot complete."""


EmailModeOverride = Literal["draft", "send"] | None


@dataclass(frozen=True)
class RunOptions:
    product: str = "groww"
    iso_week: str | None = None
    email_mode: EmailModeOverride = None
    dry_run: bool = False
    save_artifacts: bool = True
    force_refresh_ingest: bool = False
    force_delivery: bool = False


def generate_run_id(product: str, iso_week: str) -> str:
    suffix = uuid.uuid4().hex[:6]
    return f"{product}-{iso_week}-{suffix}"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _log_stage(
    stage: str,
    *,
    run_id: str,
    product: str,
    iso_week: str,
    duration_ms: float | None = None,
    **extra: object,
) -> None:
    payload: dict[str, object] = {
        "event": "pulse_run_stage",
        "stage": stage,
        "run_id": run_id,
        "product": product,
        "iso_week": iso_week,
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(duration_ms, 1)
    payload.update(extra)
    logger.info(json.dumps(payload, default=str))


def _run_artifact_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def save_run_artifacts(
    run_id: str,
    *,
    report: PulseReport,
    doc_section: DocSection,
    email_teaser: EmailTeaser,
) -> Path:
    directory = _run_artifact_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    save_report_artifact(report, directory)
    (directory / "doc_section.json").write_text(
        doc_section.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (directory / "email_teaser.json").write_text(
        email_teaser.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return directory


def load_run_artifacts(run_id: str) -> tuple[PulseReport, DocSection, EmailTeaser] | None:
    directory = _run_artifact_dir(run_id)
    report_path = directory / "pulse_report.json"
    doc_path = directory / "doc_section.json"
    email_path = directory / "email_teaser.json"
    if not (report_path.is_file() and doc_path.is_file() and email_path.is_file()):
        return None
    report = PulseReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    doc_section = DocSection.model_validate_json(doc_path.read_text(encoding="utf-8"))
    email_teaser = EmailTeaser.model_validate_json(email_path.read_text(encoding="utf-8"))
    return report, doc_section, email_teaser


def _outcome_from_completed_run(run: RunRecord) -> RunOutcome:
    doc_audit = None
    email_audit = None
    for delivery in run.deliveries:
        if delivery.channel == "google_doc" and delivery.url:
            doc_audit = DocDeliveryAudit(
                document_id=delivery.external_id or "",
                section_anchor=delivery.idempotency_key or f"{run.product}-{run.iso_week}",
                url=delivery.url,
                appended=False,
            )
        if delivery.channel == "gmail" and delivery.idempotency_key:
            email_audit = EmailDeliveryAudit(
                mode=run.email_mode or "draft",
                idempotency_key=delivery.idempotency_key,
                draft_id=delivery.external_id,
                to="",
            )
    return RunOutcome(
        run_id=run.run_id,
        product=run.product,
        iso_week=run.iso_week,
        status="skipped",
        skipped=True,
        review_count=run.review_count,
        window_weeks=run.window_weeks,
        started_at=run.started_at,
        completed_at=run.completed_at,
        doc_delivery=doc_audit,
        email_delivery=email_audit,
    )


def _run_ingest_pipeline_render(
    *,
    run_id: str,
    product: str,
    iso_week: str,
    product_config: ProductConfig,
    force_refresh_ingest: bool,
    embed_client: object | None,
    groq_client: object | None,
) -> tuple[PulseReport, DocSection, EmailTeaser]:
    pipeline_config = load_pipeline_config()

    stage_start = time.perf_counter()
    _log_stage("ingest", run_id=run_id, product=product, iso_week=iso_week)
    try:
        ingestion = ingest_product(product, force_refresh=force_refresh_ingest)
    except IngestionError as exc:
        raise OrchestratorError(str(exc)) from exc
    _log_stage(
        "ingest",
        run_id=run_id,
        product=product,
        iso_week=iso_week,
        duration_ms=(time.perf_counter() - stage_start) * 1000,
        review_count=ingestion.normalized_count,
        from_cache=ingestion.from_cache,
    )

    stage_start = time.perf_counter()
    _log_stage("pipeline", run_id=run_id, product=product, iso_week=iso_week)
    try:
        report = run_pipeline(
            ingestion.reviews,
            product=product,
            product_config=product_config,
            pipeline_config=pipeline_config,
            embed_client=embed_client,
            groq_client=groq_client,
        )
    except PipelineError as exc:
        raise OrchestratorError(str(exc)) from exc
    except Exception as exc:
        raise OrchestratorError(f"Pipeline failed during Groq summarization: {exc}") from exc
    _log_stage(
        "pipeline",
        run_id=run_id,
        product=product,
        iso_week=iso_week,
        duration_ms=(time.perf_counter() - stage_start) * 1000,
        themes=len(report.themes),
        groq_requests=report.stats.groq_requests,
    )

    stage_start = time.perf_counter()
    _log_stage("render", run_id=run_id, product=product, iso_week=iso_week)
    doc_section, email_teaser = build_outputs(
        report,
        product_config=product_config,
        iso_week=iso_week,
    )
    _log_stage(
        "render",
        run_id=run_id,
        product=product,
        iso_week=iso_week,
        duration_ms=(time.perf_counter() - stage_start) * 1000,
    )
    return report, doc_section, email_teaser


def run_pulse(
    options: RunOptions | None = None,
    *,
    ledger: RunLedger | None = None,
    mcp_client: HostedMcpTransport | None = None,
    owns_mcp_client: bool = False,
    embed_client: object | None = None,
    groq_client: object | None = None,
) -> RunOutcome:
    """
    Execute ingestion → pipeline → render → MCP delivery with ledger audit.

    Re-running the same (product, iso_week) after success is a no-op.
    After partial failure (Doc ok, Gmail fail), retries Gmail only.
    """
    opts = options or RunOptions()
    product = opts.product
    iso_week = opts.iso_week or resolve_default_iso_week()
    started_at = _utc_now()
    run_id = generate_run_id(product, iso_week)

    product_config = load_product_config(product)
    window_weeks = product_config.ingestion.window_weeks

    if opts.dry_run:
        report, doc_section, email_teaser = _run_ingest_pipeline_render(
            run_id=run_id,
            product=product,
            iso_week=iso_week,
            product_config=product_config,
            force_refresh_ingest=opts.force_refresh_ingest,
            embed_client=embed_client,
            groq_client=groq_client,
        )
        artifact_dir = None
        if opts.save_artifacts:
            artifact_dir = save_run_artifacts(
                run_id,
                report=report,
                doc_section=doc_section,
                email_teaser=email_teaser,
            )
        return RunOutcome(
            run_id=run_id,
            product=product,
            iso_week=iso_week,
            status="completed",
            review_count=report.stats.review_count,
            window_weeks=window_weeks,
            started_at=started_at,
            completed_at=_utc_now(),
            artifact_dir=str(artifact_dir) if artifact_dir else None,
        )

    store = ledger or RunLedger()
    email_mode = resolve_email_mode(product_config, opts.email_mode)
    document_id = product_config.delivery.google_doc_id
    recipients = get_email_recipients(product_config)

    _log_stage("idempotency_check", run_id="-", product=product, iso_week=iso_week)
    completed = store.find_completed_run(product, iso_week)
    if completed:
        _log_stage(
            "idempotency_skip",
            run_id=completed.run_id,
            product=product,
            iso_week=iso_week,
        )
        return _outcome_from_completed_run(completed)

    partial_run = store.find_failed_run_with_doc_delivery(product, iso_week)
    resume_from_email = partial_run is not None
    run_id = partial_run.run_id if resume_from_email else generate_run_id(product, iso_week)
    run_started_at = partial_run.started_at if resume_from_email else started_at

    if not resume_from_email:
        store.create_run(
            run_id=run_id,
            product=product,
            iso_week=iso_week,
            email_mode=email_mode,
            started_at=run_started_at,
        )

    report: PulseReport | None = None
    doc_section: DocSection | None = None
    email_teaser: EmailTeaser | None = None
    artifact_dir: Path | None = None

    try:
        if resume_from_email:
            _log_stage(
                "partial_retry",
                run_id=run_id,
                product=product,
                iso_week=iso_week,
                note="doc_already_delivered",
            )
            loaded = load_run_artifacts(run_id)
            if loaded:
                report, doc_section, email_teaser = loaded
                artifact_dir = _run_artifact_dir(run_id)
            else:
                resume_from_email = False

        if not resume_from_email:
            report, doc_section, email_teaser = _run_ingest_pipeline_render(
                run_id=run_id,
                product=product,
                iso_week=iso_week,
                product_config=product_config,
                force_refresh_ingest=opts.force_refresh_ingest,
                embed_client=embed_client,
                groq_client=groq_client,
            )
            if opts.save_artifacts:
                artifact_dir = save_run_artifacts(
                    run_id,
                    report=report,
                    doc_section=doc_section,
                    email_teaser=email_teaser,
                )

        assert report is not None and doc_section is not None and email_teaser is not None

        client = mcp_client
        close_client = False
        if client is None:
            mcp_config = load_hosted_mcp_config()
            client = HostedGoogleWorkspaceClient(mcp_config)
            close_client = True
            owns_mcp_client = True

        doc_audit: DocDeliveryAudit
        try:
            if resume_from_email and partial_run:
                doc_delivery = next(
                    d for d in partial_run.deliveries if d.channel == "google_doc"
                )
                doc_audit = DocDeliveryAudit(
                    document_id=document_id,
                    section_anchor=doc_section.anchor,
                    url=doc_delivery.url
                    or HostedGoogleWorkspaceClient.get_document_url(document_id),
                    appended=False,
                )
            else:
                stage_start = time.perf_counter()
                _log_stage("doc_delivery", run_id=run_id, product=product, iso_week=iso_week)
                doc_result = deliver_doc_section(
                    doc_section,
                    document_id=document_id,
                    client=client,  # type: ignore[arg-type]
                    force=opts.force_delivery,
                )
                store.add_delivery(
                    run_id,
                    channel="google_doc",
                    external_id=document_id,
                    url=doc_result.url,
                    idempotency_key=doc_section.anchor,
                )
                doc_audit = DocDeliveryAudit(
                    document_id=document_id,
                    section_anchor=doc_result.anchor,
                    url=doc_result.url,
                    appended=doc_result.appended,
                )
                _log_stage(
                    "doc_delivery",
                    run_id=run_id,
                    product=product,
                    iso_week=iso_week,
                    duration_ms=(time.perf_counter() - stage_start) * 1000,
                    appended=doc_result.appended,
                )

            stage_start = time.perf_counter()
            _log_stage("email_delivery", run_id=run_id, product=product, iso_week=iso_week)
            if email_mode == "send" and not client.supports_send_email():  # type: ignore[union-attr]
                raise OrchestratorError(
                    "email_mode=send requires POST /send_email on hosted MCP — "
                    "set PULSE_EMAIL_MODE=draft until send is deployed (DOC/runbook.md)"
                )
            email_result = deliver_email_teaser(
                email_teaser,
                recipients=recipients,
                client=client,  # type: ignore[arg-type]
                doc_url=doc_audit.url,
                force=opts.force_delivery,
                mode=email_mode,
            )
            store.add_delivery(
                run_id,
                channel="gmail",
                external_id=email_result.message_id or email_result.draft_id,
                url=email_result.doc_url,
                idempotency_key=email_result.idempotency_key,
            )
            _log_stage(
                "email_delivery",
                run_id=run_id,
                product=product,
                iso_week=iso_week,
                duration_ms=(time.perf_counter() - stage_start) * 1000,
                created=email_result.created,
            )
            if artifact_dir and doc_audit.url:
                resolved_teaser = apply_doc_url_to_teaser(email_teaser, doc_audit.url)
                (artifact_dir / "email_teaser.json").write_text(
                    resolved_teaser.model_dump_json(indent=2),
                    encoding="utf-8",
                )
        finally:
            if close_client and owns_mcp_client and isinstance(client, HostedGoogleWorkspaceClient):
                client.close()

        email_audit = EmailDeliveryAudit(
            mode=email_mode,
            idempotency_key=email_result.idempotency_key,
            draft_id=email_result.draft_id,
            message_id=email_result.message_id,
            to=email_result.to,
        )

        completed_at = _utc_now()
        store.mark_completed(
            run_id,
            review_count=report.stats.review_count,
            window_weeks=window_weeks,
            completed_at=completed_at,
        )

        outcome = RunOutcome(
            run_id=run_id,
            product=product,
            iso_week=iso_week,
            status="completed",
            review_count=report.stats.review_count,
            window_weeks=window_weeks,
            started_at=run_started_at,
            completed_at=completed_at,
            doc_delivery=doc_audit,
            email_delivery=email_audit,
            artifact_dir=str(artifact_dir) if artifact_dir else None,
        )
        _log_stage("complete", run_id=run_id, product=product, iso_week=iso_week)
        return outcome

    except (OrchestratorError, HostedMcpError) as exc:
        message = str(exc)
        if isinstance(exc, HostedMcpError):
            message = f"MCP delivery failed: {exc}"
        store.mark_failed(
            run_id,
            error_message=message,
            review_count=report.stats.review_count if report else None,
            window_weeks=window_weeks,
        )
        _log_stage("failed", run_id=run_id, product=product, iso_week=iso_week, error=message)
        raise OrchestratorError(message) from exc
    except Exception as exc:
        message = f"Unexpected orchestrator error: {exc}"
        store.mark_failed(
            run_id,
            error_message=message,
            review_count=report.stats.review_count if report else None,
            window_weeks=window_weeks,
        )
        _log_stage("failed", run_id=run_id, product=product, iso_week=iso_week, error=message)
        raise OrchestratorError(message) from exc
