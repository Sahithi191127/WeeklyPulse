# Phase 8 — Staging E2E Runbook

Validate the full Weekly Pulse pipeline against **real Google APIs** in staging before production send (Phase 9).

**Environment:** draft-only email · staging Google Doc · hosted MCP on Railway.

---

## Prerequisites

| Item | Check |
|------|-------|
| `GROQ_API_KEY` set | `pulse config check-secrets --skip-connectivity` |
| Hosted MCP reachable | `pulse mcp health` |
| Staging `google_doc_id` in `config/products/groww.yaml` | `pulse config validate --secrets` |
| `PULSE_EMAIL_MODE=draft` (default in YAML) | Never `send` in staging |
| Review cache available | `pulse ingest --product groww` |

---

## Staging E2E procedure

### 1. Dry-run (no Google writes)

```bash
pulse dry-run --product groww --iso-week 2026-W24
```

Confirm pipeline themes, review count, and rendered artifacts under `data/runs/{run_id}/`.

### 2. Full staging run (Doc append + Gmail draft)

```bash
pulse run --product groww --iso-week 2026-W24 --email-mode draft
```

Or automated gate test:

```bash
STAGING_E2E=1 PULSE_STAGING_ISO_WEEK=2026-W24 pytest tests/test_e2e_quality_gate.py::test_staging_live_weekly_run -v
```

### 3. Quality gate

```bash
pulse quality-gate --product groww --iso-week 2026-W24
pulse status --product groww --iso-week 2026-W24
```

### 4. Manual verification

| Check | How |
|-------|-----|
| Doc section structure | Open staging Doc; confirm heading `Groww — Weekly Review Pulse — {iso_week}`, sections: Top themes, Real user quotes, Action ideas, Who this helps |
| Heading anchor | Anchor key `{product}-{iso_week}` matches ledger `doc_delivery.anchor` |
| Email draft | Gmail draft has 3–5 theme bullets, subject line, Doc deep link in body |
| Doc deep link | Link opens the staging Doc (same document id as config) |
| PII | No raw emails/phones in Doc body or email text |
| Idempotency | Re-run same week → `Run skipped (already completed)`; no duplicate Doc section or draft |

```bash
pulse run --product groww --iso-week 2026-W24   # second run = no-op
```

### 5. Backfill load test (3 weeks, idempotent)

```bash
pulse backfill --product groww --from 2026-W21 --to 2026-W23
pulse backfill --product groww --from 2026-W21 --to 2026-W23   # all skipped
```

CI runs an equivalent mock test in `tests/test_e2e_quality_gate.py::test_backfill_three_weeks_idempotent`.

---

## Groq rate-limit backoff (manual 429 test)

Automated: `tests/test_summarizer.py::test_summarizer_retries_on_groq_429` simulates 429 → retry → success.

**Manual procedure** (optional, Developer plan):

1. Temporarily lower `request_interval_seconds` in `config/pipeline.yaml` to `0`.
2. Run pipeline on cached reviews with many clusters (or trigger parallel requests outside the agent — not recommended).
3. Watch logs for `Groq rate limit, backing off Ns`.
4. Restore `request_interval_seconds: 2` after test.

Expected behavior: up to 3 retries with exponential backoff (2s, 4s); run aborts if all retries fail.

---

## Metrics to review per run

After each staging run, inspect:

| Metric | Source |
|--------|--------|
| Review count | `pulse status` or quality-gate metrics |
| Cluster count / noise % | `data/runs/{run_id}/pulse_report.json` → `stats` |
| Groq requests / tokens | `stats.groq_requests`, `stats.groq_*_tokens` |
| Stage durations | JSON logs: `event=pulse_run_stage`, `duration_ms` |
| Delivery ids | `pulse status --json` |

```bash
pulse quality-gate --product groww --iso-week 2026-W24 --json
```

---

## Exit criteria (Phase 8)

| Check | Automated | Manual |
|-------|-----------|--------|
| Doc section matches expected structure | `pulse quality-gate` | Open staging Doc |
| Email teaser + Doc link | quality-gate + status | Open Gmail draft |
| Re-run same week = no duplicate | `test_backfill_three_weeks_idempotent` | Second `pulse run` |
| `pulse status` shows delivery ids | CLI tests | — |
| CI green | GitHub Actions | — |
| Stakeholder sample approved | — | [sign-off-checklist.md](./sign-off-checklist.md) |

---

## Troubleshooting

| Symptom | Action |
|---------|--------|
| MCP auth error | Refresh OAuth on Railway MCPServer; `pulse mcp health` |
| Duplicate Doc section | Check anchor ledger under `data/deliveries/docs/`; use `pulse status` |
| Partial failure (Doc ok, email fail) | Re-run same week — orchestrator retries Gmail only |
| Quality gate fails on PII | Inspect `pulse_report.json` themes; scrubber runs before embed/LLM |
