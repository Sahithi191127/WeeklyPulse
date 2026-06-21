# Production Operations Runbook — Weekly Pulse

Operational guide for scheduled production runs (Phase 9). Staging procedures: [staging-e2e.md](./staging-e2e.md).

---

## Schedule

| Item | Value |
|------|-------|
| **Cadence** | Weekly, Monday 09:00 IST |
| **Scheduler** | GitHub Actions [`.github/workflows/weekly-pulse.yml`](../.github/workflows/weekly-pulse.yml) |
| **Manual trigger** | Actions → Weekly Pulse (Production) → Run workflow |
| **ISO week** | `PULSE_ISO_WEEK_POLICY=auto` (previous week on Monday IST) |

---

## Environment matrix

| Variable | Production | Staging | Local |
|----------|------------|---------|-------|
| `PULSE_ENV` | `production` | `staging` | `local` (default) |
| `PULSE_EMAIL_MODE` | `send` when MCP supports it; **`draft` until then** | `draft` | unset / dry-run |
| `GOOGLE_DOC_ID` | Production Doc secret | Staging Doc | Optional test Doc |
| `PULSE_EMAIL_TO` | Stakeholder list | Test inbox | — |

Copy [`.env.production.example`](../.env.production.example) for scheduler secrets.

---

## Preflight (before first production run)

```bash
export PULSE_ENV=production
export PULSE_EMAIL_MODE=draft   # or send when MCPServer has /send_email
pulse config production-check --product groww
pulse mcp health
pulse ingest --product groww
pulse dry-run --product groww
```

**MCPServer requirements (Railway):**

- `REQUIRE_APPROVAL=false` for automation
- `GOOGLE_TOKEN_JSON` valid (Docs + Gmail scopes)
- Optional `API_KEY` ↔ `MCP_API_KEY` in pulse agent

---

## Normal weekly operation

Automated (GitHub Actions):

1. `pulse config production-check`
2. `pulse run --product groww`
3. `pulse quality-gate --product groww --iso-week {week}`

Manual equivalent:

```bash
pulse run --product groww
pulse status --product groww --iso-week 2026-W24
pulse quality-gate --product groww --iso-week 2026-W24
```

Confirm ledger `status=completed` and stakeholders received email (or draft until send is enabled).

---

## Enabling production send

MCPServer v1 supports **draft only** (`POST /create_email_draft`). To enable send:

1. Deploy `POST /send_email` on MCPServer (same body as draft).
2. Verify: `pulse mcp health` lists send endpoint.
3. Set `PULSE_EMAIL_MODE=send` in workflow / scheduler.
4. Run `pulse config production-check` — must pass send capability check.
5. One manual `pulse run --product groww --email-mode send` before enabling scheduler send.

Until then, production workflow uses **`PULSE_EMAIL_MODE=draft`** — ops sends draft manually or upgrades MCPServer.

---

## Failure handling

### Run failed (ingest / pipeline / LLM)

| Symptom | Action |
|---------|--------|
| Ingestion error | `pulse ingest --force-refresh`; check Play Store access |
| Pipeline / Groq error | Check `GROQ_API_KEY`, rate limits; retry after backoff |
| Ledger `failed`, no deliveries | Safe to re-run full `pulse run --iso-week …` |

```bash
pulse run --product groww --iso-week 2026-W24
```

### Partial failure (Doc ok, Gmail failed)

Ledger shows `failed` with `google_doc` delivery recorded.

```bash
pulse run --product groww --iso-week 2026-W24
```

Orchestrator retries Gmail only; Doc append is idempotent (no duplicate section).

### MCP / Google errors

| HTTP | Meaning | Action |
|------|---------|--------|
| 403 | `REQUIRE_APPROVAL=true` on Railway | Set `REQUIRE_APPROVAL=false` |
| 401 | Bad `MCP_API_KEY` | Fix secret |
| 5xx | MCP outage | Retry; check Railway logs |

Refresh Google OAuth on MCPServer if token expired.

### Duplicate prevention

- Same `(product, iso_week)`: ledger + local delivery ledgers skip re-write.
- Force override (debug only): `pulse run --force` / `pulse deliver-doc --force`.

---

## Monitoring and audit

| Signal | Where |
|--------|-------|
| Run status | `pulse status --product groww --iso-week YYYY-Www --json` |
| SQLite ledger | `data/ledger.sqlite` → tables `runs`, `deliveries` |
| Artifacts | `data/runs/{run_id}/pulse_report.json` |
| Stage logs | JSON logs: `event=pulse_run_stage` |
| CI artifacts | GitHub Actions → Weekly Pulse → run artifacts |

**Metrics per run:** review count, cluster count, noise %, Groq requests/tokens — `pulse quality-gate --json`.

---

## Alerts (recommended)

Configure on scheduler failure (GitHub Actions email/Slack, PagerDuty, etc.):

- Workflow `Weekly Pulse (Production)` failed
- `pulse config production-check` failed on Monday preflight

On-call should retry manually after fixing root cause.

---

## Backfill / recovery

```bash
pulse backfill --product groww --from 2026-W20 --to 2026-W23
```

Completed weeks are skipped automatically.

---

## Contacts

| Role | Responsibility |
|------|----------------|
| On-call eng | Retry failed runs, MCP/OAuth issues |
| Product | Sign-off on report quality ([sign-off-checklist.md](./sign-off-checklist.md)) |
| MCP owner | Railway MCPServer deploy, OAuth, send endpoint |

---

## Exit criteria (Phase 9)

- [ ] Two consecutive weekly production runs succeed without manual intervention
- [ ] Run ledger shows `completed` for both weeks
- [ ] Stakeholders receive report (draft or send)
- [ ] This runbook shared with on-call owner
