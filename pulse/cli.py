"""Pulse CLI — run, ingest, pipeline, dry-run, status."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from pulse.agent import OrchestratorError, RunOptions, run_pulse
from pulse.config import (
    SecretValidationError,
    get_email_recipients,
    load_hosted_mcp_config,
    load_pipeline_config,
    load_product_config,
    validate_all_configs,
    validate_agent_secrets,
    validate_delivery_config,
    validate_groq_key,
    validate_hosted_mcp_connectivity,
    validate_mcp_env_files,
    validate_embedding_config,
)
from pulse.ingestion import IngestionError, ingest_product
from pulse.iso_week import IsoWeekError, iter_iso_weeks, parse_iso_week, resolve_default_iso_week
from pulse.ledger import RunLedger
from pulse.ledger.models import RunRecord
from pulse.pipeline import PipelineError, run_pipeline_for_product
from pulse.render import build_outputs, resolve_default_iso_week

app = typer.Typer(
    name="pulse",
    help="Weekly Product Review Pulse — Groww Play Store insights",
    no_args_is_help=True,
)

config_app = typer.Typer(help="Validate configuration and secrets.")
mcp_app = typer.Typer(help="Hosted MCP connectivity and Doc delivery.")
app.add_typer(config_app, name="config")
app.add_typer(mcp_app, name="mcp")


def _validate_email_mode(email_mode: str | None) -> str | None:
    if email_mode is None:
        return None
    normalized = email_mode.strip().lower()
    if normalized not in ("draft", "send"):
        typer.echo(f"Invalid --email-mode {email_mode!r} (use draft or send)", err=True)
        raise typer.Exit(code=1)
    return normalized


def _resolve_iso_week(iso_week: str | None) -> str:
    if iso_week:
        try:
            parse_iso_week(iso_week)
        except IsoWeekError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        return iso_week
    return resolve_default_iso_week()


def _print_report_summary(report) -> None:
    typer.echo(
        f"Pipeline OK: product={report.product}, themes={len(report.themes)}, "
        f"reviews={report.stats.review_count}, noise={report.stats.noise_pct}%, "
        f"clusters={report.stats.cluster_count}"
    )
    if report.stats.fallbacks_used:
        typer.echo(f"Clustering fallbacks: {', '.join(report.stats.fallbacks_used)}")
    if report.stats.groq_requests:
        typer.echo(
            f"Groq: requests={report.stats.groq_requests}, "
            f"tokens={report.stats.groq_input_tokens + report.stats.groq_output_tokens}"
        )
    for theme in report.themes:
        typer.echo(f"  - {theme.theme_name} ({theme.cluster_size} reviews, {theme.avg_rating:.1f}★)")


def _print_run_outcome(outcome, *, verbose: bool = True) -> None:
    if outcome.skipped:
        typer.echo(
            f"Run skipped (already completed): product={outcome.product}, "
            f"iso_week={outcome.iso_week}, run_id={outcome.run_id}"
        )
    else:
        typer.echo(
            f"Run {outcome.status}: product={outcome.product}, iso_week={outcome.iso_week}, "
            f"run_id={outcome.run_id}"
        )
    if outcome.review_count is not None:
        typer.echo(f"  reviews={outcome.review_count}, window_weeks={outcome.window_weeks}")
    if outcome.doc_delivery and verbose:
        typer.echo(
            f"  doc: anchor={outcome.doc_delivery.section_anchor}, "
            f"url={outcome.doc_delivery.url}"
        )
    if outcome.email_delivery and verbose:
        typer.echo(
            f"  email: mode={outcome.email_delivery.mode}, "
            f"key={outcome.email_delivery.idempotency_key}, "
            f"draft_id={outcome.email_delivery.draft_id}"
        )
    if outcome.artifact_dir:
        typer.echo(f"  artifacts={outcome.artifact_dir}")


def _format_run_status(run: RunRecord) -> dict:
    doc = next((d for d in run.deliveries if d.channel == "google_doc"), None)
    email = next((d for d in run.deliveries if d.channel == "gmail"), None)
    return {
        "run_id": run.run_id,
        "product": run.product,
        "iso_week": run.iso_week,
        "status": run.status,
        "email_mode": run.email_mode,
        "review_count": run.review_count,
        "window_weeks": run.window_weeks,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "error_message": run.error_message,
        "doc_delivery": {
            "document_id": doc.external_id,
            "anchor": doc.idempotency_key,
            "url": doc.url,
        }
        if doc
        else None,
        "email_delivery": {
            "idempotency_key": email.idempotency_key,
            "draft_id": email.external_id,
            "doc_url": email.url,
        }
        if email
        else None,
    }


@app.command("run")
def run(
    product: str = typer.Option("groww", "--product", "-p", help="Product slug"),
    iso_week: str | None = typer.Option(
        None, "--iso-week", help="ISO week e.g. 2026-W23 (default: policy in PULSE_ISO_WEEK_POLICY)"
    ),
    email_mode: str | None = typer.Option(
        None, "--email-mode", help="Override email mode: draft | send (or PULSE_EMAIL_MODE)"
    ),
    force_refresh: bool = typer.Option(
        False, "--force-refresh", help="Re-scrape reviews even if today's cache exists"
    ),
    force: bool = typer.Option(
        False, "--force", help="Force Doc/email delivery even if idempotency keys exist"
    ),
    output_json: bool = typer.Option(False, "--json", help="Print RunOutcome JSON"),
) -> None:
    """Run the weekly pulse for a product and ISO week."""
    email_mode = _validate_email_mode(email_mode)
    week = _resolve_iso_week(iso_week)

    try:
        validate_all_configs(product, require_secrets=True)
        validate_delivery_config(load_product_config(product))
    except (FileNotFoundError, ValueError, SecretValidationError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        outcome = run_pulse(
            RunOptions(
                product=product,
                iso_week=week,
                email_mode=email_mode,  # type: ignore[arg-type]
                force_refresh_ingest=force_refresh,
                force_delivery=force,
            )
        )
    except OrchestratorError as exc:
        typer.echo(f"Run failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output_json:
        typer.echo(json.dumps(json.loads(outcome.model_dump_json()), indent=2))
    else:
        _print_run_outcome(outcome)


@app.command("ingest")
def ingest(
    product: str = typer.Option("groww", "--product", "-p", help="Product slug"),
    force_refresh: bool = typer.Option(
        False, "--force-refresh", help="Re-scrape even if today's cache exists"
    ),
) -> None:
    """Fetch and cache Play Store reviews for a product."""
    try:
        load_product_config(product)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        result = ingest_product(product, force_refresh=force_refresh)
    except IngestionError as exc:
        typer.echo(f"Ingestion failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    source = "cache" if result.from_cache else "scrape"
    typer.echo(
        f"Ingestion OK ({source}): product={result.product}, "
        f"normalized={result.normalized_count}, "
        f"raw={result.raw_count}, cache={result.cache_dir}"
    )


@app.command("pipeline")
def pipeline(
    product: str = typer.Option("groww", "--product", "-p"),
    cache_date: str | None = typer.Option(None, "--cache-date", help="YYYY-MM-DD"),
    skip_llm: bool = typer.Option(
        False, "--skip-llm", help="Run scrub/embed/cluster only"
    ),
    output_json: bool = typer.Option(False, "--json", help="Print full PulseReport JSON"),
) -> None:
    """Run analysis pipeline on cached normalized reviews."""
    from datetime import date

    try:
        pipeline_config = load_pipeline_config()
        validate_embedding_config(pipeline_config)
        if not skip_llm:
            validate_groq_key()
        load_product_config(product)
    except (FileNotFoundError, ValueError, SecretValidationError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    parsed_date = date.fromisoformat(cache_date) if cache_date else None
    try:
        report = run_pipeline_for_product(
            product,
            cache_date=parsed_date,
            skip_llm=skip_llm,
        )
    except PipelineError as exc:
        typer.echo(f"Pipeline failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output_json:
        typer.echo(json.dumps(json.loads(report.model_dump_json()), indent=2))
    else:
        _print_report_summary(report)


@app.command("backfill")
def backfill(
    product: str = typer.Option("groww", "--product", "-p"),
    from_week: str = typer.Option(..., "--from", help="Start ISO week YYYY-Www"),
    to_week: str = typer.Option(..., "--to", help="End ISO week YYYY-Www"),
    email_mode: str | None = typer.Option(
        None, "--email-mode", help="Override email mode: draft | send"
    ),
    force_refresh: bool = typer.Option(
        False, "--force-refresh", help="Re-scrape reviews for each week run"
    ),
    output_json: bool = typer.Option(False, "--json", help="Print summary JSON array"),
) -> None:
    """Backfill pulse runs for a range of ISO weeks (sequential; skips completed)."""
    email_mode = _validate_email_mode(email_mode)

    try:
        validate_all_configs(product, require_secrets=True)
        validate_delivery_config(load_product_config(product))
        weeks = iter_iso_weeks(from_week, to_week)
    except (FileNotFoundError, ValueError, SecretValidationError, IsoWeekError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    ledger = RunLedger()
    results: list[dict] = []
    failures = 0

    for week in weeks:
        if ledger.find_completed_run(product, week):
            message = f"Skipping {week} (already completed)"
            typer.echo(message)
            results.append({"iso_week": week, "status": "skipped", "reason": "completed"})
            continue

        typer.echo(f"Running backfill for {week}...")
        try:
            outcome = run_pulse(
                RunOptions(
                    product=product,
                    iso_week=week,
                    email_mode=email_mode,  # type: ignore[arg-type]
                    force_refresh_ingest=force_refresh,
                ),
                ledger=ledger,
            )
        except OrchestratorError as exc:
            typer.echo(f"Backfill failed for {week}: {exc}", err=True)
            results.append({"iso_week": week, "status": "failed", "error": str(exc)})
            failures += 1
            continue

        status = "skipped" if outcome.skipped else outcome.status
        results.append(
            {
                "iso_week": week,
                "status": status,
                "run_id": outcome.run_id,
            }
        )
        if not output_json:
            _print_run_outcome(outcome, verbose=False)

    if output_json:
        typer.echo(json.dumps({"weeks": results, "failures": failures}, indent=2))
    else:
        typer.echo(
            f"Backfill done: {len(weeks)} week(s), "
            f"{sum(1 for r in results if r['status'] == 'skipped')} skipped, "
            f"{failures} failed"
        )

    if failures:
        raise typer.Exit(code=1)


@app.command("render")
def render(
    product: str = typer.Option("groww", "--product", "-p"),
    iso_week: str | None = typer.Option(None, "--iso-week"),
    report_json: str | None = typer.Option(
        None, "--report-json", help="Path to PulseReport JSON (default: run pipeline)"
    ),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="When running pipeline inline"),
    output_json: bool = typer.Option(False, "--json", help="Print DocSection + EmailTeaser JSON"),
) -> None:
    """Render DocSection and EmailTeaser from a PulseReport (no MCP delivery)."""
    from pulse.pipeline.models import PulseReport

    try:
        product_config = load_product_config(product)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    if report_json:
        report = PulseReport.model_validate_json(Path(report_json).read_text(encoding="utf-8"))
    else:
        try:
            pipeline_config = load_pipeline_config()
            validate_embedding_config(pipeline_config)
            if not skip_llm:
                validate_groq_key()
        except (FileNotFoundError, ValueError, SecretValidationError) as exc:
            typer.echo(f"Configuration error:\n{exc}", err=True)
            raise typer.Exit(code=1) from exc
        try:
            report = run_pipeline_for_product(product, skip_llm=skip_llm)
        except PipelineError as exc:
            typer.echo(f"Pipeline failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    week = _resolve_iso_week(iso_week)
    doc_section, email_teaser = build_outputs(
        report,
        product_config=product_config,
        iso_week=week,
    )

    if output_json:
        payload = {
            "iso_week": week,
            "doc_section": json.loads(doc_section.model_dump_json()),
            "email_teaser": json.loads(email_teaser.model_dump_json()),
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(
            f"Render OK: anchor={doc_section.anchor}, "
            f"content_chars={len(doc_section.content)}, "
            f"email_subject={email_teaser.subject!r}"
        )


@app.command("dry-run")
def dry_run(
    product: str = typer.Option("groww", "--product", "-p"),
    iso_week: str | None = typer.Option(None, "--iso-week"),
    force_refresh: bool = typer.Option(
        False, "--force-refresh", help="Re-scrape reviews even if today's cache exists"
    ),
    output_json: bool = typer.Option(False, "--json", help="Print RunOutcome JSON"),
) -> None:
    """Run full pipeline + render (no MCP delivery or ledger writes)."""
    week = _resolve_iso_week(iso_week)

    try:
        validate_all_configs(product, require_secrets=True)
        load_product_config(product)
    except (FileNotFoundError, ValueError, SecretValidationError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        outcome = run_pulse(
            RunOptions(
                product=product,
                iso_week=week,
                dry_run=True,
                force_refresh_ingest=force_refresh,
            )
        )
    except OrchestratorError as exc:
        typer.echo(f"Dry-run failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output_json:
        typer.echo(json.dumps(json.loads(outcome.model_dump_json()), indent=2))
    else:
        typer.echo(f"Dry-run OK for iso_week={week}, run_id={outcome.run_id}")
        if outcome.review_count is not None:
            typer.echo(f"  reviews={outcome.review_count}, window_weeks={outcome.window_weeks}")
        if outcome.artifact_dir:
            typer.echo(f"  artifacts={outcome.artifact_dir}")


@app.command("quality-gate")
def quality_gate(
    product: str = typer.Option("groww", "--product", "-p"),
    iso_week: str = typer.Option(..., "--iso-week", help="ISO week YYYY-Www"),
    output_json: bool = typer.Option(False, "--json", help="Print quality-gate JSON"),
) -> None:
    """Validate a completed run against Phase 8 quality-gate checks."""
    from pulse.iso_week import parse_iso_week
    from pulse.quality_gate import validate_completed_run

    try:
        parse_iso_week(iso_week)
        load_product_config(product)
    except (FileNotFoundError, ValueError, IsoWeekError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    result = validate_completed_run(product=product, iso_week=iso_week)
    if output_json:
        typer.echo(
            json.dumps(
                {
                    "passed": result.passed,
                    "errors": result.errors,
                    "warnings": result.warnings,
                    "metrics": result.metrics,
                },
                indent=2,
            )
        )
    else:
        if result.metrics:
            typer.echo("Metrics:")
            for key, value in result.metrics.items():
                typer.echo(f"  {key}: {value}")
        for warning in result.warnings:
            typer.echo(f"Warning: {warning}")
        if result.passed:
            typer.echo(f"Quality gate PASSED for {product} {iso_week}")
        else:
            typer.echo("Quality gate FAILED:", err=True)
            for error in result.errors:
                typer.echo(f"  - {error}", err=True)

    if not result.passed:
        raise typer.Exit(code=1)


@app.command("status")
def status(
    product: str = typer.Option("groww", "--product", "-p"),
    iso_week: str = typer.Option(..., "--iso-week", help="ISO week YYYY-Www"),
    output_json: bool = typer.Option(False, "--json", help="Print status JSON"),
) -> None:
    """Show run ledger status and delivery ids for an ISO week."""
    try:
        parse_iso_week(iso_week)
        load_product_config(product)
    except (FileNotFoundError, ValueError, IsoWeekError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    ledger = RunLedger()
    run = ledger.find_latest_run(product, iso_week)
    if run is None:
        typer.echo(f"No run found for product={product}, iso_week={iso_week}")
        raise typer.Exit(code=1)

    payload = _format_run_status(run)
    if output_json:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Run: {run.run_id}")
    typer.echo(f"Status: {run.status}")
    typer.echo(f"Started: {run.started_at.isoformat() if run.started_at else '-'}")
    typer.echo(f"Completed: {run.completed_at.isoformat() if run.completed_at else '-'}")
    if run.review_count is not None:
        typer.echo(f"Reviews: {run.review_count} (window {run.window_weeks} weeks)")
    if run.email_mode:
        typer.echo(f"Email mode: {run.email_mode}")
    if run.error_message:
        typer.echo(f"Error: {run.error_message}")

    doc = next((d for d in run.deliveries if d.channel == "google_doc"), None)
    if doc:
        typer.echo(
            f"Doc delivery: document_id={doc.external_id}, anchor={doc.idempotency_key}, "
            f"url={doc.url}"
        )
    email = next((d for d in run.deliveries if d.channel == "gmail"), None)
    if email:
        typer.echo(
            f"Email delivery: idempotency_key={email.idempotency_key}, "
            f"draft_id={email.external_id}, doc_url={email.url}"
        )


@mcp_app.command("health")
def mcp_health() -> None:
    """Check connectivity to the hosted Google Workspace MCP server."""
    from pulse.agent import HostedGoogleWorkspaceClient, HostedMcpError

    try:
        config = load_hosted_mcp_config()
        with HostedGoogleWorkspaceClient(config) as client:
            health = client.health_check()
            docs_capabilities = client.list_docs_capabilities()
            gmail_capabilities = client.list_gmail_capabilities()
    except (HostedMcpError, FileNotFoundError, ValueError) as exc:
        typer.echo(f"MCP health check failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(
        f"MCP OK: url={config.base_url}, service={health.service}, "
        f"status={health.status}, docs={docs_capabilities}, gmail={gmail_capabilities}"
    )
    if health.has_refresh_token is False:
        typer.echo(
            "WARNING: MCP token missing refresh_token — Doc/Gmail delivery will fail. "
            "Run authenticate.py in MCPServer and update Railway GOOGLE_TOKEN_JSON.",
            err=True,
        )
        raise typer.Exit(code=1)
    if health.google_token_usable is False:
        typer.echo(
            f"WARNING: MCP Google token not usable: {health.google_token_error}",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command("deliver-doc")
def deliver_doc(
    product: str = typer.Option("groww", "--product", "-p"),
    iso_week: str | None = typer.Option(None, "--iso-week"),
    doc_section_json: str | None = typer.Option(
        None, "--doc-section-json", help="Path to DocSection JSON"
    ),
    render_json: str | None = typer.Option(
        None, "--render-json", help="Path to render output JSON (doc_section key)"
    ),
    force: bool = typer.Option(
        False, "--force", help="Append even if anchor was delivered before"
    ),
    output_json: bool = typer.Option(False, "--json", help="Print delivery result JSON"),
) -> None:
    """Append a DocSection to Google Docs via hosted MCP (Phase 4)."""
    from pulse.agent import DocsDeliveryError, HostedGoogleWorkspaceClient, HostedMcpError, deliver_doc_section
    from pulse.render.models import DocSection

    try:
        product_config = load_product_config(product)
        validate_delivery_config(product_config)
        mcp_config = load_hosted_mcp_config()
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    doc_section: DocSection | None = None
    if doc_section_json:
        doc_section = DocSection.model_validate_json(
            Path(doc_section_json).read_text(encoding="utf-8")
        )
    elif render_json:
        payload = json.loads(Path(render_json).read_text(encoding="utf-8"))
        doc_section = DocSection.model_validate(payload["doc_section"])
    else:
        fixture = Path("tests/fixtures/expected_doc_section.json")
        if not fixture.is_file():
            typer.echo(
                "Provide --doc-section-json or --render-json (or run from repo with fixtures).",
                err=True,
            )
            raise typer.Exit(code=1)
        doc_section = DocSection.model_validate_json(fixture.read_text(encoding="utf-8"))

    document_id = product_config.delivery.google_doc_id
    try:
        with HostedGoogleWorkspaceClient(mcp_config) as client:
            result = deliver_doc_section(
                doc_section,
                document_id=document_id,
                client=client,
                force=force,
            )
    except (HostedMcpError, DocsDeliveryError) as exc:
        typer.echo(f"Doc delivery failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output_json:
        typer.echo(json.dumps(json.loads(result.model_dump_json()), indent=2))
    else:
        action = "appended" if result.appended else "skipped (already delivered)"
        typer.echo(
            f"Doc delivery OK: anchor={result.anchor}, {action}, "
            f"url={result.url}, chars={result.content_chars}"
        )


@app.command("deliver-email")
def deliver_email(
    product: str = typer.Option("groww", "--product", "-p"),
    to: str | None = typer.Option(
        None, "--to", help="Override recipient(s), comma-separated"
    ),
    email_teaser_json: str | None = typer.Option(
        None, "--email-teaser-json", help="Path to EmailTeaser JSON"
    ),
    render_json: str | None = typer.Option(
        None, "--render-json", help="Path to render output JSON (email_teaser key)"
    ),
    doc_url: str | None = typer.Option(
        None, "--doc-url", help="Doc deep link (default: from Phase 4 delivery ledger)"
    ),
    force: bool = typer.Option(
        False, "--force", help="Create draft even if idempotency key was used before"
    ),
    output_json: bool = typer.Option(False, "--json", help="Print delivery result JSON"),
) -> None:
    """Create a Gmail draft from EmailTeaser via hosted MCP (Phase 5)."""
    from pulse.agent import (
        EmailDeliveryError,
        HostedGoogleWorkspaceClient,
        HostedMcpError,
        deliver_email_teaser,
        resolve_doc_url,
    )
    from pulse.render.models import DocSection, EmailTeaser

    try:
        product_config = load_product_config(product)
        validate_delivery_config(product_config)
        mcp_config = load_hosted_mcp_config()
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    if to:
        recipients = [address.strip() for address in to.split(",") if address.strip()]
    else:
        recipients = get_email_recipients(product_config)

    email_teaser: EmailTeaser | None = None
    doc_section: DocSection | None = None
    if email_teaser_json:
        email_teaser = EmailTeaser.model_validate_json(
            Path(email_teaser_json).read_text(encoding="utf-8")
        )
    elif render_json:
        payload = json.loads(Path(render_json).read_text(encoding="utf-8"))
        email_teaser = EmailTeaser.model_validate(payload["email_teaser"])
        if "doc_section" in payload:
            doc_section = DocSection.model_validate(payload["doc_section"])
    else:
        fixture = Path("tests/fixtures/expected_email_teaser.json")
        doc_fixture = Path("tests/fixtures/expected_doc_section.json")
        if not fixture.is_file():
            typer.echo(
                "Provide --email-teaser-json or --render-json (or run from repo with fixtures).",
                err=True,
            )
            raise typer.Exit(code=1)
        email_teaser = EmailTeaser.model_validate_json(fixture.read_text(encoding="utf-8"))
        if doc_fixture.is_file():
            doc_section = DocSection.model_validate_json(doc_fixture.read_text(encoding="utf-8"))

    resolved_doc_url = doc_url
    if resolved_doc_url is None and doc_section is not None:
        resolved_doc_url = resolve_doc_url(
            document_id=product_config.delivery.google_doc_id,
            anchor=doc_section.anchor,
        )
    elif resolved_doc_url is None:
        resolved_doc_url = resolve_doc_url(document_id=product_config.delivery.google_doc_id)

    try:
        with HostedGoogleWorkspaceClient(mcp_config) as client:
            result = deliver_email_teaser(
                email_teaser,
                recipients=recipients,
                client=client,
                doc_url=resolved_doc_url,
                force=force,
            )
    except (HostedMcpError, EmailDeliveryError) as exc:
        typer.echo(f"Email delivery failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output_json:
        typer.echo(json.dumps(json.loads(result.model_dump_json()), indent=2))
    else:
        action = "created" if result.created else "skipped (already delivered)"
        typer.echo(
            f"Email delivery OK: idempotency_key={result.idempotency_key}, {action}, "
            f"to={result.to!r}, subject={result.subject!r}, doc_url={result.doc_url}"
        )


@config_app.command("production-check")
def config_production_check(
    product: str = typer.Option("groww", "--product", "-p"),
    skip_connectivity: bool = typer.Option(
        False, "--skip-connectivity", help="Skip live MCP health check"
    ),
) -> None:
    """Preflight validation for production scheduled runs (Phase 9)."""
    from pulse.config import validate_production_readiness

    if skip_connectivity:
        typer.echo(
            "Warning: --skip-connectivity skips MCP token/send checks",
            err=True,
        )
    try:
        if skip_connectivity:
            validate_all_configs(product, require_secrets=True)
            validate_delivery_config(load_product_config(product))
        else:
            validate_production_readiness(product)
    except (FileNotFoundError, ValueError, SecretValidationError) as exc:
        typer.echo(f"Production check failed:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Production readiness OK for product={product}")


@config_app.command("validate")
def config_validate(
    product: str = typer.Option("groww", "--product", "-p"),
    secrets: bool = typer.Option(
        False,
        "--secrets",
        help="Also validate GROQ_API_KEY, hosted MCP URL, and MCP connectivity",
    ),
) -> None:
    """Load YAML/MCP configs; optionally check secrets."""
    try:
        validate_all_configs(product, require_secrets=secrets)
        typer.echo(f"Configuration OK for product={product}")
        if secrets:
            typer.echo("Secrets and hosted MCP connectivity OK")
    except (FileNotFoundError, ValueError, SecretValidationError) as exc:
        typer.echo(f"Configuration error:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc


@config_app.command("check-secrets")
def config_check_secrets(
    skip_connectivity: bool = typer.Option(
        False, "--skip-connectivity", help="Skip live MCP health check"
    ),
) -> None:
    """Validate agent API keys and hosted MCP configuration."""
    errors: list[str] = []
    try:
        validate_agent_secrets()
    except SecretValidationError as exc:
        errors.extend(exc.messages)
    try:
        validate_mcp_env_files()
    except SecretValidationError as exc:
        errors.extend(exc.messages)
    if not skip_connectivity:
        try:
            validate_hosted_mcp_connectivity()
        except SecretValidationError as exc:
            errors.extend(exc.messages)

    if errors:
        typer.echo("Secret validation failed:\n" + "\n".join(errors), err=True)
        raise typer.Exit(code=1)

    typer.echo("All required secrets and hosted MCP checks passed")
