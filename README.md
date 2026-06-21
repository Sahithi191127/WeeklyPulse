# Weekly Product Review Pulse

Automated weekly insight report from **Groww** Google Play Store reviews, delivered via the hosted **[Google MCP Server](https://github.com/Sahithi191127/MCPServer)** on Railway.

## Documentation

| Document | Description |
|----------|-------------|
| [DOC/problemstatement.md](DOC/problemstatement.md) | Product intent and requirements |
| [DOC/Architecture.md](DOC/Architecture.md) | Technical architecture |
| [DOC/implementation-plan.md](DOC/implementation-plan.md) | Phase-wise build plan |
| [DOC/mcp-api.md](DOC/mcp-api.md) | Hosted MCP REST contract |
| [DOC/edge-cases.md](DOC/edge-cases.md) | Fallbacks and failure modes |
| [DOC/staging-e2e.md](DOC/staging-e2e.md) | Staging E2E runbook (Phase 8) |
| [DOC/sign-off-checklist.md](DOC/sign-off-checklist.md) | Stakeholder sign-off before production |
| [DOC/runbook.md](DOC/runbook.md) | Production operations runbook (Phase 9) |

## Prerequisites

- **Python 3.11+**
- **Groq API key** for summarization (`GROQ_API_KEY`)
- **Embeddings:** local BGE-small (no API key). Optional `OPENAI_API_KEY` only if `embedding.provider: openai`
- **Google Doc id** in `config/products/groww.yaml`
- **Hosted MCP:** [MCPServer](https://github.com/Sahithi191127/MCPServer) deployed at `https://web-production-c5ea8.up.railway.app` (Google OAuth lives on that server)

## Setup

### 1. Python (pulse agent)

```bash
cd WeeklyPulse
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

### 2. Environment variables

```bash
copy .env.example .env
# Edit .env — at minimum set GROQ_API_KEY
```

| Variable | Required | Notes |
|----------|----------|-------|
| `GROQ_API_KEY` | Full pipeline | Groq summarization |
| `MCP_SERVER_URL` | No | Defaults to Railway URL in `config/mcp/servers.json` |
| `MCP_API_KEY` | If Railway `API_KEY` set | Sent as `X-API-Key` header |
| `PULSE_EMAIL_MODE` | No | Override `draft` / `send` for `pulse run` |
| `PULSE_ENV` | No | `local` (default), `staging`, or `production` — controls email default |
| `PULSE_ISO_WEEK_POLICY` | No | `auto` (default), `current`, or `previous` — see below |

### 3. Configuration

- Product: `config/products/groww.yaml` — set `delivery.google_doc_id`
- Pipeline: `config/pipeline.yaml`
- MCP URL: `config/mcp/servers.json`

## CLI

```bash
pulse --help

# Config + secrets + MCP connectivity
pulse config validate
pulse config validate --secrets

# Hosted MCP health
pulse mcp health

# Ingestion & analysis
pulse ingest --product groww
pulse pipeline --product groww --skip-llm
pulse pipeline --product groww
pulse render --product groww --json
pulse dry-run --product groww

# Weekly run (Phase 6–7 orchestrator)
pulse run --product groww                          # default ISO week (see policy below)
pulse run --product groww --iso-week 2026-W23
pulse run --product groww --email-mode draft
pulse backfill --product groww --from 2026-W20 --to 2026-W23
pulse status --product groww --iso-week 2026-W23
pulse quality-gate --product groww --iso-week 2026-W23

# Doc delivery (requires real google_doc_id)
pulse deliver-doc --product groww --doc-section-json tests/fixtures/expected_doc_section.json

# Email draft
pulse deliver-email --product groww --email-teaser-json tests/fixtures/expected_email_teaser.json
pulse deliver-email --product groww --to you@example.com
```

### Default ISO week

When `--iso-week` is omitted, `pulse run`, `pulse dry-run`, and `pulse render` use `PULSE_ISO_WEEK_POLICY`:

| Policy | Behavior |
|--------|----------|
| `auto` (default) | Previous ISO week on **Monday IST**; current week on other days |
| `current` | Always the ISO week containing today (IST) |
| `previous` | Always the prior ISO week |

### Weekly scheduling (Monday 09:00 IST)

**Cron** (server timezone must be IST, or adjust the hour):

```cron
0 9 * * 1 cd /path/to/WeeklyPulse && .venv/bin/pulse run --product groww >> /var/log/pulse.log 2>&1
```

**GitHub Actions** (`.github/workflows/weekly-pulse.yml`):

```yaml
name: Weekly Pulse
on:
  schedule:
    - cron: "30 3 * * 1"  # 09:00 IST = 03:30 UTC (Mon)
  workflow_dispatch:

jobs:
  pulse:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: pulse run --product groww
        env:
          GROQ_API_KEY: ${{ secrets.GROQ_API_KEY }}
          MCP_SERVER_URL: https://web-production-c5ea8.up.railway.app
          MCP_API_KEY: ${{ secrets.MCP_API_KEY }}
          PULSE_EMAIL_MODE: draft
          PULSE_ISO_WEEK_POLICY: auto
```

**Google Cloud Scheduler**: HTTP target or Cloud Run job invoking the same `pulse run --product groww` with secrets in Secret Manager.

### Staging E2E (Phase 8)

Before production, run the staging checklist in [DOC/staging-e2e.md](DOC/staging-e2e.md):

```bash
pulse dry-run --product groww
pulse run --product groww --iso-week 2026-W24 --email-mode draft
pulse quality-gate --product groww --iso-week 2026-W24
pulse run --product groww --iso-week 2026-W24   # idempotency — should skip
```

Live staging test (requires secrets): `STAGING_E2E=1 pytest -m staging -v`

### Production (Phase 9)

1. Copy [`.env.production.example`](.env.production.example) secrets into GitHub Actions / scheduler.
2. Preflight: `PULSE_ENV=production pulse config production-check --product groww`
3. Enable [`.github/workflows/weekly-pulse.yml`](.github/workflows/weekly-pulse.yml) (Monday 09:00 IST).
4. Operations: [DOC/runbook.md](DOC/runbook.md)

Production email uses **`PULSE_EMAIL_MODE=draft`** until MCPServer adds `POST /send_email`; then set `PULSE_EMAIL_MODE=send`.

## Hosted MCP server

WeeklyPulse does **not** embed Google OAuth. Delivery goes through the separate [MCPServer](https://github.com/Sahithi191127/MCPServer) project:

| Endpoint | Purpose |
|----------|---------|
| `POST /append_to_doc` | Append plain-text weekly section |
| `POST /create_email_draft` | Gmail draft (Phase 5) |
| `GET /health` | Connectivity check |

Production Railway deploy should set `REQUIRE_APPROVAL=false` so automated runs are not blocked. Details: [DOC/mcp-api.md](DOC/mcp-api.md).

## Tests

```bash
pytest
```

## Project layout

```
config/           Product, pipeline, MCP URL
pulse/            Python agent (ingestion, pipeline, render, delivery)
data/             Cached reviews — gitignored
tests/            Unit tests
DOC/              Design documents
mcp-servers/      Legacy local stubs (not used for delivery)
```

## Development status

See [DOC/implementation-plan.md](DOC/implementation-plan.md). Phases 0–9 implemented (production send pending MCPServer `/send_email` endpoint).
