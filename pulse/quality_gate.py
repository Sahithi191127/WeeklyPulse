"""Staging E2E and quality-gate validators (Phase 8)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pulse.agent.orchestrator import RUNS_DIR, load_run_artifacts
from pulse.ledger.models import RunOutcome, RunRecord
from pulse.ledger.store import RunLedger
from pulse.pipeline.models import PulseReport
from pulse.pipeline.scrubber import _EMAIL_RE, _PHONE_RE, _ID_RE
from pulse.render.models import DocSection, EmailTeaser

REQUIRED_DOC_SECTIONS = (
    "Top themes",
    "Real user quotes",
    "Action ideas",
    "Who this helps",
)

# Raw PII must not appear in published outputs (scrubbed tokens are OK).
_RAW_PII_PATTERNS = (
    _EMAIL_RE,
    _PHONE_RE,
    re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b"),
    re.compile(r"\b\d{10,12}\b"),
)


@dataclass
class QualityGateResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)

    def raise_if_failed(self) -> None:
        if not self.passed:
            raise QualityGateError("\n".join(self.errors))


class QualityGateError(Exception):
    """Raised when quality-gate validation fails."""


def find_raw_pii(text: str) -> list[str]:
    """Return descriptions of raw PII patterns found in text."""
    hits: list[str] = []
    for pattern in _RAW_PII_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append(f"raw PII matched {pattern.pattern!r}: {match.group(0)!r}")
    return hits


def validate_doc_section(
    doc_section: DocSection,
    *,
    product: str,
    iso_week: str,
) -> list[str]:
    errors: list[str] = []
    expected_anchor = f"{product}-{iso_week}"
    if doc_section.anchor != expected_anchor:
        errors.append(f"anchor={doc_section.anchor!r}, expected {expected_anchor!r}")
    if doc_section.heading_text != doc_section.content.split("\n", 1)[0]:
        errors.append("heading_text does not match first line of content")
    for section in REQUIRED_DOC_SECTIONS:
        if section not in doc_section.content:
            errors.append(f"missing Doc section: {section!r}")
    errors.extend(find_raw_pii(doc_section.content))
    return errors


def validate_email_teaser(
    email_teaser: EmailTeaser,
    *,
    product: str,
    iso_week: str,
    doc_url: str | None = None,
) -> list[str]:
    errors: list[str] = []
    expected_key = f"{product}-{iso_week}-email"
    if email_teaser.idempotency_key != expected_key:
        errors.append(
            f"idempotency_key={email_teaser.idempotency_key!r}, expected {expected_key!r}"
        )
    if not email_teaser.theme_bullets:
        errors.append("email teaser has no theme bullets")
    if doc_url:
        if doc_url not in email_teaser.text_body:
            errors.append("doc deep link missing from email text_body")
        if doc_url not in email_teaser.cta_url:
            errors.append("doc deep link missing from email cta_url")
    errors.extend(find_raw_pii(email_teaser.text_body))
    errors.extend(find_raw_pii(email_teaser.html_body))
    return errors


def validate_run_record(run: RunRecord) -> list[str]:
    errors: list[str] = []
    if run.status == "completed":
        channels = {delivery.channel for delivery in run.deliveries}
        if "google_doc" not in channels:
            errors.append("completed run missing google_doc delivery")
        if "gmail" not in channels:
            errors.append("completed run missing gmail delivery")
        for delivery in run.deliveries:
            if delivery.channel == "google_doc" and not delivery.url:
                errors.append("google_doc delivery missing url")
            if delivery.channel == "gmail" and not delivery.idempotency_key:
                errors.append("gmail delivery missing idempotency_key")
    return errors


def extract_run_metrics(report: PulseReport) -> dict[str, object]:
    stats = report.stats
    return {
        "review_count": stats.review_count,
        "cluster_count": stats.cluster_count,
        "noise_pct": stats.noise_pct,
        "groq_requests": stats.groq_requests,
        "groq_input_tokens": stats.groq_input_tokens,
        "groq_output_tokens": stats.groq_output_tokens,
        "groq_total_tokens": stats.groq_input_tokens + stats.groq_output_tokens,
        "fallbacks_used": stats.fallbacks_used,
        "theme_count": len(report.themes),
    }


def validate_run_artifacts(
    artifact_dir: Path,
    *,
    product: str,
    iso_week: str,
    doc_url: str | None = None,
) -> QualityGateResult:
    """Validate saved run artifacts under data/runs/{run_id}/."""
    loaded = load_run_artifacts(artifact_dir.name)
    if loaded is None:
        report_path = artifact_dir / "pulse_report.json"
        doc_path = artifact_dir / "doc_section.json"
        email_path = artifact_dir / "email_teaser.json"
        if not (report_path.is_file() and doc_path.is_file() and email_path.is_file()):
            return QualityGateResult(
                passed=False,
                errors=[f"missing artifacts in {artifact_dir}"],
            )
        report = PulseReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        doc_section = DocSection.model_validate_json(doc_path.read_text(encoding="utf-8"))
        email_teaser = EmailTeaser.model_validate_json(email_path.read_text(encoding="utf-8"))
    else:
        report, doc_section, email_teaser = loaded

    errors: list[str] = []
    errors.extend(validate_doc_section(doc_section, product=product, iso_week=iso_week))
    errors.extend(
        validate_email_teaser(
            email_teaser,
            product=product,
            iso_week=iso_week,
            doc_url=doc_url,
        )
    )
    return QualityGateResult(
        passed=not errors,
        errors=errors,
        metrics=extract_run_metrics(report),
    )


def validate_completed_run(
    *,
    product: str,
    iso_week: str,
    ledger: RunLedger | None = None,
    doc_url: str | None = None,
) -> QualityGateResult:
    """Validate ledger record + optional artifacts for a completed staging run."""
    store = ledger or RunLedger()
    run = store.find_latest_run(product, iso_week)
    if run is None:
        return QualityGateResult(passed=False, errors=[f"no run found for {product} {iso_week}"])

    errors = validate_run_record(run)
    warnings: list[str] = []
    metrics: dict[str, object] = {
        "run_id": run.run_id,
        "status": run.status,
        "review_count": run.review_count,
        "window_weeks": run.window_weeks,
    }

    if run.status != "completed":
        errors.append(f"run status is {run.status!r}, expected 'completed'")
    if run.error_message:
        warnings.append(run.error_message)

    doc_delivery = next((d for d in run.deliveries if d.channel == "google_doc"), None)
    email_delivery = next((d for d in run.deliveries if d.channel == "gmail"), None)
    resolved_doc_url = doc_url or (doc_delivery.url if doc_delivery else None)

    artifact_dir = RUNS_DIR / run.run_id
    if artifact_dir.is_dir():
        artifact_result = validate_run_artifacts(
            artifact_dir,
            product=product,
            iso_week=iso_week,
            doc_url=resolved_doc_url,
        )
        errors.extend(artifact_result.errors)
        metrics.update(artifact_result.metrics)
    else:
        warnings.append(f"no artifacts at {artifact_dir}")

    if resolved_doc_url and email_delivery:
        pass  # deep link validated when artifacts exist

    return QualityGateResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        metrics=metrics,
    )


def validate_outcome(outcome: RunOutcome, *, product: str, iso_week: str) -> QualityGateResult:
    """Validate a RunOutcome from orchestrator (mock or live)."""
    errors: list[str] = []
    if outcome.skipped:
        return QualityGateResult(passed=True, warnings=["run was skipped (already completed)"])
    if outcome.status != "completed":
        errors.append(f"outcome status={outcome.status!r}")
    if outcome.doc_delivery is None:
        errors.append("missing doc_delivery audit")
    else:
        if outcome.doc_delivery.section_anchor != f"{product}-{iso_week}":
            errors.append(
                f"doc anchor={outcome.doc_delivery.section_anchor!r}, "
                f"expected {product}-{iso_week!r}"
            )
    if outcome.email_delivery is None:
        errors.append("missing email_delivery audit")
    else:
        expected_key = f"{product}-{iso_week}-email"
        if outcome.email_delivery.idempotency_key != expected_key:
            errors.append(
                f"email key={outcome.email_delivery.idempotency_key!r}, expected {expected_key!r}"
            )
    metrics: dict[str, object] = {
        "run_id": outcome.run_id,
        "review_count": outcome.review_count,
        "window_weeks": outcome.window_weeks,
    }
    if outcome.artifact_dir:
        artifact_result = validate_run_artifacts(
            Path(outcome.artifact_dir),
            product=product,
            iso_week=iso_week,
            doc_url=outcome.doc_delivery.url if outcome.doc_delivery else None,
        )
        errors.extend(artifact_result.errors)
        metrics.update(artifact_result.metrics)
    return QualityGateResult(passed=not errors, errors=errors, metrics=metrics)
