# Weekly Product Review Pulse — Architecture

This document describes the technical architecture for the Groww Play Store review pulse: components, data flows, MCP integration, idempotency, and operational concerns. It extends [problemstatement.md](./problemstatement.md).

**Implementation status:** Phase 0 ✅ · Phase 1 ✅ · Phase 2 ✅ · Phase 3 ✅ · Phases 4–5 (hosted MCP integration) pending. See [implementation-plan.md](./implementation-plan.md) for phase exit criteria and validated live-data assumptions.

1. Goals and Constraints
Goal
Architectural implication
Weekly insight report from Play Store reviews
Batch pipeline, not streaming
Google Doc as system of record
Append-only sections with stable anchors
Email as notification, not duplicate report
Teaser + deep link to Doc heading
MCP-only delivery to Google Workspace
Pulse agent never holds Google OAuth or calls REST directly
Idempotent weekly runs
Run ledger + deterministic section keys
Auditable history
Persist run metadata and delivery IDs
Safe LLM usage
PII scrubbing, quote validation, token/cost caps

Current scope: Groww · Google Play Store · Google Workspace delivery via **hosted MCP server** (`https://web-production-c5ea8.up.railway.app`).

2. System Context
Stakeholders
This Repository
External
Google Play Store
Google Workspace APIs
Groq API
llama-3.3-70b-versatile
BGE-small (local, sentence-transformers)
Pulse CLI / Scheduler
Pulse Agent
MCP Host
Play Store Ingestion
Analysis Pipeline
Report & Email Renderer
Run Ledger
Hosted Google Workspace MCP (Railway)
Weekly Review Pulse — Groww
Google Doc
Stakeholder Inboxes
The pulse agent orchestrates ingestion, analysis, rendering, and delivery. It connects to the **hosted MCP server** over HTTPS (SSE/HTTP transport) as an MCP client. Google OAuth, token refresh, and Workspace API calls are confined to that server — not in the pulse agent or this repo.

3. Logical Layers
Layer 4 — Delivery (MCP)
Layer 3 — Output Generation
Layer 2 — Reasoning
Layer 1 — Data Retrieval
Play Store Scraper
Review Normalizer
PII Scrubber
Embedder
UMAP + HDBSCAN
Groq Summarizer
Quote Validator
Doc Section Builder
Email Teaser Builder
Docs MCP Tools
Gmail MCP Tools
Layer
Responsibility
Must not
Data retrieval
Fetch and normalize Play Store reviews for Groww
Call Google Workspace APIs
Reasoning
Cluster, summarize, validate quotes
Write to Docs or Gmail
Output generation
Build plain-text Doc content and email HTML/text
Hold Google OAuth
Delivery
Append Doc section, send/draft email
Contain clustering/LLM logic


4. Repository Layout (as built)

```
WeeklyPulse/
├── DOC/
│   ├── problemstatement.md
│   ├── Architecture.md
│   ├── implementation-plan.md
│   └── edge-cases.md
├── config/
│   ├── products/
│   │   └── groww.yaml          # Play Store app id, doc id, recipients
│   ├── pipeline.yaml           # cluster params, LLM limits
│   └── mcp/
│       ├── servers.json        # Hosted MCP URL + transport (Railway)
│       └── mcp.env.example     # MCP_SERVER_URL, optional MCP_API_KEY
├── mcp-servers/                # Legacy local stubs (not used for delivery)
│   ├── google-docs-mcp/
│   └── gmail-mcp/
├── pulse/
│   ├── cli.py                  # run, ingest, backfill, dry-run, status
│   ├── config.py
│   ├── agent/
│   │   ├── orchestrator.py     # Phase 6
│   │   └── mcp_client.py
│   ├── ingestion/              # Phase 1 ✅
│   │   ├── play_store.py       # Scraper + pagination
│   │   ├── normalizer.py       # ≥8 words, English, no emoji
│   │   ├── cache.py            # reviews.json / reviews_normalized.json
│   │   ├── service.py          # ingest orchestration
│   │   └── models.py           # Review, RawReview ({ text, rating })
│   ├── pipeline/               # Phase 2
│   │   ├── scrubber.py
│   │   ├── embeddings.py
│   │   ├── clustering.py
│   │   ├── summarizer.py
│   │   └── quote_validator.py
│   ├── render/                 # Phase 3
│   └── ledger/                 # Phase 6
├── data/cache/{product}/{date}/  # gitignored
└── tests/
```

This layout keeps the pulse pipeline and MCP client configuration in-repo while Google Workspace delivery runs on the hosted MCP server.

5. End-to-End Run Flow
Gmail MCPDocs MCPRun LedgerRendererPipelineIngestionOrchestratorPulse CLIGmail MCPDocs MCPRun LedgerRendererPipelineIngestionOrchestratorPulse CLIalt[already completed][new or failed retry]run --product groww --iso-week 2026-W23check idempotency(groww, 2026-W23)prior delivery idsskip (no-op success)fetch_reviews(window=8-12w)Review[]analyze(reviews)PulseReport(themes, quotes, actions)build_outputs(report, iso_week)DocSection, EmailTeaserappend_section(doc_id, anchor, blocks)heading_id, doc_url_fragmentsend_or_draft(teaser, deep_link, idempotency_key)message_id / draft_idrecord_run(metadata, delivery_ids)success + audit summary
Run inputs
Parameter
Description
Example
product
Product slug
groww
iso_week
ISO 8601 week
2026-W23
window_weeks
Rolling review window
10 (within 8–12 configurable range)
dry_run
Skip MCP writes
false
email_mode
draft or send
draft in staging

Run outputs (audit record)
{
  "run_id": "groww-2026-W23-abc123",
  "product": "groww",
  "iso_week": "2026-W23",
  "review_count": 872,
  "window_weeks": 10,
  "started_at": "2026-06-08T03:30:00+05:30",
  "completed_at": "2026-06-08T03:42:11+05:30",
  "doc_delivery": {
    "document_id": "...",
    "section_anchor": "groww-2026-W23",
    "heading_id": "...",
    "url": "https://docs.google.com/document/d/...#heading=..."
  },
  "email_delivery": {
    "mode": "draft",
    "message_id": "...",
    "idempotency_key": "groww-2026-W23-email"
  },
  "status": "completed"
}


6. Play Store Ingestion (Phase 1 ✅)

**CLI:** `pulse ingest --product groww [--force-refresh]`

### Responsibilities

- Resolve Groww’s Play Store listing from `config/products/groww.yaml` (`play_store.app_id`).
- Scrape public reviews within the configured date window (8–12 weeks, default 10).
- Paginate until window boundary or no more pages (`published_at` used at scrape time only — not stored on disk).
- Normalize to a canonical `Review` model and cache.

### Review models (on disk)

Both cached files store **`{ text, rating }` only** — no `review_id` or `published_at`.

| File | Model | Fields |
|------|-------|--------|
| `reviews.json` | `RawReview` | `text`, `rating` (1–5) |
| `reviews_normalized.json` | `Review` | `text`, `rating` (1–5) — Phase 2 input |

### Phase 1 normalization rules

Applied when building `reviews_normalized.json`:

1. Drop reviews with **&lt; 8 words** (`min_words`).
2. Drop reviews containing **emoji**.
3. Drop reviews **not in English** (`allowed_language: en`, via langdetect).
4. Dedupe by hash of **`(text, rating)`** before filtering.

Drop counts are recorded in `manifest.json` → `normalization`.

### Validated live metrics (Groww, 2026-06-12, 10-week window)

| Metric | Value |
|--------|-------|
| Cache path | `data/cache/groww/2026-06-12/` |
| Raw (`reviews.json`) | 4,371 |
| Normalized | 1,266 (~29%) |
| Window | 2026-04-03 → 2026-06-12 |
| 1–2★ share (normalized) | ~50% |
| Median length | 18 words / 101 chars |
| Max length | 104 words / 500 chars |

**Typical normalization drops:** ~2,800 too short · ~170 emoji · ~140 non-English.

**Recurring themes:** brokerage/charges, trading, options, charts, customer support, app updates.

### Design decisions

- Cache under `data/cache/{product}/{date}/`: `reviews.json`, `reviews_normalized.json`, `manifest.json`.
- Same-day cache hit skips re-scrape unless `--force-refresh`.
- Rate limiting + exponential backoff on scrape errors; ingestion failure writes `manifest.json` with `status: incomplete`.
- `ReviewSource` protocol allows future sources (App Store out of v1).
- **Known v1 limitation:** broken English may pass langdetect (`wrost`, Hinglish fragments).

7. Analysis Pipeline
Input: list[Review] with { text, rating } from normalized cache or ingestion.
ML floor: If normalized review count <20, abort before embedding (orchestrator may also enforce min_reviews from product config).
7.1 PII scrubbing
Run before embedding, LLM calls, and publishing.
Pattern class
Action
Email addresses
Redact → [EMAIL]
Phone numbers (IN formats)
Redact → [PHONE]
Long numeric sequences (PAN/Aadhaar-like)
Redact → [ID]
URLs with tokens
Redact path/query
Financial amounts (10k, lakhs, $…)
Keep in v1 — useful theme signal, not treated as PII

Scrubbed text is used for embedding, LLM prompts, Doc output, and quote validation. Unscrubbed text stays in `reviews.json` only (gitignored). The quote validator always compares against scrubbed cluster text.

**Validated:** normalized reviews contain names, amounts, and order details — PII scrubbing is required before embed/LLM.
7.2 Embeddings and clustering
no
yes
Scrubbed reviews
text + rating
count ≥ 20?
Abort run
BAAI/bge-small-en-v1.5
batch encode (local)
UMAP
random_state=42
HDBSCAN
min_cluster_size=5
Rank: score = size × (6 − avg_rating)
Fallbacks?
Select 5–8 samples
per top cluster
Top N clusters → Groq
Parameter
Typical default
Config key
Embedding provider / model
sentence-transformers / BAAI/bge-small-en-v1.5 (OpenAI optional)
pipeline.embedding.*
Embedding cache key
sha256(scrubbed_text + rating)
stable per review text + rating
UMAP n_neighbors
15
pipeline.clustering.umap.n_neighbors
UMAP n_components
5
pipeline.clustering.umap.n_components
UMAP random_state
42
pipeline.clustering.umap.random_state
HDBSCAN min_cluster_size
5
pipeline.clustering.hdbscan.min_cluster_size
Top clusters to summarize
3–5
pipeline.summarization.max_themes
Samples per cluster
5–8 (medoid + diversity)
pipeline.summarization.max_samples_per_cluster

Cluster ranking: score = cluster_size × (6 − avg_rating) — prioritizes large low-star complaint themes (Groww normalized cache is ~50% 1–2★).

Noise cluster (label = −1) reviews are excluded from theme generation unless volume exceeds a configurable threshold.

**Validated on live data:** probe clustering shows high noise (~50% on keyword proxy); real embeddings improve separation but **fallbacks are mandatory** (not optional).

Clustering fallbacks (see [edge-cases.md](./edge-cases.md) §3):

| Condition | Behavior |
|-----------|----------|
| All noise | Lower `min_cluster_size` once; if still all noise, abort or rating-stratified LLM pass |
| One cluster &gt; 80% | **Rating split** (1–2★ vs 4–5★) before re-rank |
| Many micro-clusters | Take top `max_themes` by score only |
| Mixed sentiment in cluster | Optional: prefix `"Rating: N."` in embed input |

**Pipeline input:** `data/cache/groww/{date}/reviews_normalized.json` from the latest complete cache.

7.3 LLM summarization (Groq)

Provider: Groq — **`llama-3.3-70b-versatile`**. Embeddings run locally via **BGE-small**; only summarization uses Groq (`GROQ_API_KEY`).

Call pattern: One Groq request per top cluster (not one mega-prompt). **Sequential only** — no parallel LLM requests.

**Groq Developer plan limits (confirmed in console):**

| Limit | Console | Pipeline implication |
|-------|---------|---------------------|
| Requests / minute | **30** | `request_interval_seconds: 2` |
| Requests / day | **1,000** | ~5–10 req/run (~1% of cap) |
| Tokens / minute | **12,000** | **Tightest constraint** — keep each request &lt; ~10K tokens |
| Tokens / day | **100,000** | `max_tokens_per_run: 12,000` (~6–8% of cap per run) |

**Per weekly Groww run (estimated):** 5–10 Groq requests · 6–8K tokens total.

**Local embeddings:** `BAAI/bge-small-en-v1.5` via `sentence-transformers`, batch 64 — no API key; first run downloads the model from Hugging Face. Optional: `embedding.provider: openai` + `OPENAI_API_KEY`.

Each per-cluster request receives:
5–8 representative review samples (scrubbed, truncated to max_review_chars)
Cluster size and average rating
Untrusted-data framing; strict JSON schema output
Output schema (per theme):
{
  "theme_name": "App performance & bugs",
  "summary": "Lag and crashes during trading hours; session timeouts.",
  "quotes": ["The app freezes exactly when the market opens..."],
  "action_ideas": [
    {
      "title": "Stabilize peak-time performance",
      "detail": "Scale infra during market hours; improve crash visibility."
    }
  ]
}

Prompt safety and budget:
Reviews wrapped as untrusted data (e.g. XML/markdown fenced blocks).
System instruction: ignore instructions embedded in review text.
Pre-flight token estimate; if over budget, drop longest samples first.
Retry 429/529 with exponential backoff (max 3).
Log per run: requests made, input/output tokens, headroom vs daily caps.
Re-prompt once per cluster if all quotes fail (counts toward RPM/RPD); omit theme if still invalid.
Typical dry-run on ~1,266 normalized reviews: ≤10 LLM requests, ≤12K total tokens (usually ~6–8K).
7.4 Quote validation
Every Groq-produced quote must pass validation before inclusion in the report:
Normalize whitespace and punctuation on quote and candidate review texts.
Require case-insensitive substring match against at least one scrubbed review in the same cluster (full scrubbed corpus as fallback).
Accept ellipsis truncation (... / …) as prefix match when the LLM shortens a long quote.
Typos and Hinglish-in-English: case-insensitive match only — no translation required.
Quotes failing validation are dropped and logged; if a theme loses all quotes, re-prompt once or omit the theme.
This prevents hallucinated “user quotes” from reaching stakeholders.

8. Output Generation
8.1 Google Doc section structure
Each weekly run appends one plain-text section to Weekly Review Pulse — Groww (`DocSection.content`):

```
Groww — Weekly Review Pulse — 2026-W23

Period: Last 10 weeks (rolling) · Source: Google Play Store · Generated: 2026-06-08 IST

Top themes

- App performance & bugs — Lag, crashes during trading hours...
...

Real user quotes

- "The app freezes exactly when the market opens..."

Action ideas

- Stabilize peak-time performance — Scale infra during market hours...

Who this helps

- Product — Prioritize roadmap from recurring themes...
```

The orchestrator passes this plain text (not raw HTML, not styled blocks) to Docs MCP. The MCP server appends via `insertText` — no heading or bullet styles in v1.
8.2 Section anchor (idempotency)
Concept
Value
Anchor key
{product}-{iso_week} e.g. groww-2026-W23
Heading text
Groww — Weekly Review Pulse — 2026-W23
Stored metadata
heading_id, document revision_id after write

Idempotent Doc write behavior:
Docs MCP searches the document for an existing section whose first line matches `heading_text` (or contains anchor key).
If found → return existing URL; do not append again.
If not found → append `content` at end (or configured insertion point).
8.3 Email teaser
Email body is intentionally short:
Subject: Groww Weekly Review Pulse — 2026-W23
Body: 3–5 bullet theme headlines + one-line context
CTA: Read full report → deep link to Doc section (#heading={heading_id} or equivalent)
Footer: generation timestamp, review window, link to full Doc
Full report content lives only in the Doc.

9. MCP Server Architecture

**Hosted server (production):** [MCPServer](https://github.com/Sahithi191127/MCPServer) on Railway — `https://web-production-c5ea8.up.railway.app`

A single deployed MCP server on Railway exposes Google Workspace tools for Docs and Gmail. The pulse agent connects remotely via **HTTP JSON REST** (FastAPI). See [mcp-api.md](./mcp-api.md) for the live endpoint mapping.

```
Pulse Agent (MCP Client)
        │  HTTPS (SSE / HTTP)
        ▼
Hosted Google Workspace MCP (Railway)
        ├── Docs tools → Google Docs API
        └── Gmail tools → Gmail API
```

**In-repo `mcp-servers/`:** Phase 0 stubs only; not used for delivery. Do not run local stdio servers in production.

### 9.1 Google Docs MCP — tools
Tool
Purpose
Key inputs
Key outputs
find_section_by_anchor
Idempotency lookup
document_id, anchor
found, heading_id, url_fragment
append_section
Add weekly section (plain text)
document_id, anchor, content, insert_at_end
revision_id, url
get_document_url
Resolve shareable link
document_id
url

**Credential handling:** OAuth client id/secret, refresh token, and Docs scopes are configured on the **Railway MCP server** — not in the pulse agent repo. The agent only needs network access to `https://web-production-c5ea8.up.railway.app` and any MCP auth token required by that deployment.

Required scopes (on hosted server): `https://www.googleapis.com/auth/documents`

### 9.2 Gmail MCP — tools
Tool
Purpose
Key inputs
Key outputs
check_idempotency
Prevent duplicate sends
idempotency_key
already_sent, message_id?
create_draft
Staging default
to[], subject, html_body, text_body, idempotency_key
draft_id
send_email
Production send
same as draft
message_id

Idempotency key format: `{product}-{iso_week}-email` (e.g. `groww-2026-W23-email`).

Idempotency is enforced by the hosted MCP server (and complemented by the pulse run ledger in Phase 6).

Required scopes (on hosted server): `gmail.compose` and/or `gmail.send` depending on draft vs send policy.

### 9.3 Pulse agent MCP client

The agent:

1. Connects to `https://web-production-c5ea8.up.railway.app` using the transport defined in `config/mcp/servers.json`.
2. Discovers tools via MCP protocol (Docs + Gmail on the same server).
3. Calls tools in order: `find_section_by_anchor` → `append_section` (if needed) → `check_idempotency` → `create_draft` / `send_email`.
4. Never imports Google API client libraries for delivery.

Example agent config (`config/mcp/servers.json`):

```json
{
  "mcpServers": {
    "google-workspace": {
      "url": "https://web-production-c5ea8.up.railway.app",
      "transport": "sse"
    }
  }
}
```

Environment variables (pulse agent / scheduler):

| Variable | Required | Purpose |
|----------|----------|---------|
| `MCP_SERVER_URL` | Yes (or in `servers.json`) | Hosted MCP base URL |
| `MCP_API_KEY` | If required by deployment | Auth to Railway MCP endpoint |
| `GROQ_API_KEY` | Yes (full run) | Summarization only |


10. Run Ledger and Audit
Central run ledger (SQLite recommended) owned by the pulse agent, written after successful MCP delivery.
Table: runs
Column
Description
run_id
UUID
product
groww
iso_week
2026-W23
status
pending, completed, failed
review_count
int
window_weeks
int
started_at, completed_at
timestamps
error_message
nullable

Table: deliveries
Column
Description
run_id
FK → runs
channel
google_doc, gmail
external_id
heading_id, message_id, draft_id
url
Doc or Gmail link
idempotency_key
nullable

Unique constraint: (product, iso_week) on runs where status = completed — enforces at-most-one successful run per week at the orchestrator level, complementing MCP-level checks.

11. Configuration
Product config — config/products/groww.yaml
product: groww
display_name: Groww
play_store:
  app_id: com.nextbillion.groww  # example; verify at build time
ingestion:
  window_weeks: 10
  min_reviews: 20
  max_reviews: 5000
  min_words: 8
  allowed_language: en
delivery:
  google_doc_id: "<SHARED_DOC_ID>"
  email:
    recipients:
      - product-leads@example.com
      - support-leads@example.com
    default_mode: draft  # draft | send

Pipeline config — config/pipeline.yaml
embedding:
  provider: sentence-transformers
  model: BAAI/bge-small-en-v1.5
  batch_size: 64
clustering:
  umap:
    n_neighbors: 15
    n_components: 5
    metric: cosine
  hdbscan:
    min_cluster_size: 5
    min_samples: 3
summarization:
  provider: groq
  model: llama-3.3-70b-versatile
  max_themes: 5
  max_tokens_per_run: 12000
  max_samples_per_cluster: 8
  max_output_tokens_per_theme: 800
  request_interval_seconds: 2
safety:
  scrub_pii: true
  max_review_chars: 2000

Environment-specific overrides via env vars (e.g. PULSE_EMAIL_MODE=send, GROQ_API_KEY for summarization). OpenAI key only if `embedding.provider: openai`.

12. CLI and Scheduling

CLI commands:

| Command | Description | Status |
|---------|-------------|--------|
| `pulse ingest --product groww [--force-refresh]` | Fetch/cache Play Store reviews | Phase 1 ✅ |
| `pulse run --product groww [--iso-week YYYY-Www]` | Full weekly run | Phase 6 |
| `pulse backfill --product groww --from 2026-W01 --to 2026-W20` | Sequential backfill | Phase 7 |
| `pulse dry-run --product groww` | Pipeline + render; skip MCP | Phase 7 |
| `pulse status --product groww --iso-week 2026-W23` | Ledger + delivery ids | Phase 7 |
| `pulse config validate [--secrets]` | Validate YAML + optional API keys | Phase 0 ✅ |

Default ISO week: week containing the run date, or previous complete week if running Monday morning IST before reviews stabilize (configurable policy).
Scheduler
Cron / GitHub Actions / Cloud Scheduler invokes pulse run --product groww weekly (e.g. Monday 09:00 IST).
Scheduler passes `GROQ_API_KEY` (and `MCP_SERVER_URL` / `MCP_API_KEY` if not in config); Google OAuth secrets stay on the Railway MCP server only.

13. Security and Safety
Risk
Mitigation
Google OAuth leakage
Credentials only on Railway MCP host; pulse agent holds MCP URL/key at most
PII in reports
Scrubber before LLM and publish
Prompt injection via reviews
Data/non-instruction framing; no tool execution from review text
Hallucinated quotes
Substring validator against source reviews
Runaway LLM cost / Groq rate limits
max_tokens_per_run (12K), sequential requests (2s interval), 429 backoff; Groq TPM 12K is tightest
High HDBSCAN noise on Groww data
Mandatory clustering fallbacks; rating split; log noise % per run
Duplicate stakeholder email
Idempotency key + ledger + Docs anchor
Scraping abuse / blocks
Rate limits, retries, user-agent policy
Broken English passes langdetect
Accept v1 limitation; ranking formula deprioritizes generic praise clusters


14. Error Handling and Partial Failure
Failure point
Behavior
Ingestion fails
Abort; no Doc/email; ledger failed
Pipeline/LLM fails
Abort; no Doc/email; ledger failed
Doc append succeeds, Gmail fails
Ledger failed with partial delivery; retry safe via idempotency (Doc no-op, Gmail retried)
Gmail succeeds, ledger write fails
Log critical alert; MCP idempotency still prevents duplicate email on retry

Retries: orchestrator may retry transient MCP errors with exponential backoff (max 3). Non-transient errors (auth, invalid doc id) fail fast.

15. Observability
Signal
Mechanism
Structured logs
JSON logs per stage with run_id, product, iso_week
Metrics
Review count (raw + normalized), normalization drop counts, cluster count, HDBSCAN noise %, Groq requests/tokens, embedding batch count, duration per stage
Artifacts
Optional JSON report snapshot in data/runs/{run_id}/
Audit queries
CLI status + SQL against ledger


16. Environments
Environment
Email mode
Doc target
Notes
Local dev
draft
Test Doc id
dry-run available
Staging
draft
Staging Doc
Requires explicit --send to override
Production
send
Production Doc
Scheduler default


17. Testing Strategy
Layer
Approach
Ingestion (Phase 1 ✅)
Fixture JSON snapshots; no live scrape in CI; 25 unit tests passing
Scrubber / validator
Table-driven tests on synthetic PII and quotes
Clustering
Golden-file tests on fixed embedding inputs
Summarizer
Mock Groq client; schema validation; rate-limit retry tests
Docs/Gmail MCP integration
Connectivity + tool contract tests against hosted MCP (mocked in CI; manual against Railway in staging)
Orchestrator
Integration test: full run with MCP mocks + ledger idempotency
E2E (manual)
One dry-run and one draft email against real Google APIs in staging


18. Future Expansion (Out of Scope for v1)
Architectural extension points already implied by the design:
Extension
Touch points
Additional products
New config/products/*.yaml; reuse pipeline + MCP
App Store RSS
New ingestion/app_store.py implementing ReviewSource
Multi-source merge
Fan-in before embed step; source dimension on Review
BI dashboard
Read from ledger + exported JSON; Doc remains canonical
Richer MCP
Additional tools only if pulse needs them; avoid generic Workspace scope


19. Architecture Decision Summary
Decision
Choice
Rationale
Delivery to Google
Hosted MCP on Railway (`web-production-c5ea8.up.railway.app`)
OAuth isolated on deployed server; pulse agent is MCP client only
Doc as source of truth
Append sections with anchors
History + idempotency + stakeholder link target
Email content
Teaser + deep link
Avoid duplicate maintenance
Clustering
UMAP + HDBSCAN
Unsupervised theme discovery without fixed taxonomy
Cluster ranking
size × (6 − avg_rating)
Surfaces actionable low-star complaint themes
Summarization LLM
Groq llama-3.3-70b-versatile (Developer plan)
Sequential per-cluster calls; 30 RPM / 12K TPM / 100K TPD confirmed
Embeddings
BAAI/bge-small-en-v1.5 (local)
No API key; batch-friendly for ~1,266 normalized reviews
Cache on disk
reviews.json + reviews_normalized.json
{text, rating} only; manifest includes normalization stats
Quote trust
Post-LLM substring validation against scrubbed text
Prevents fabricated user voice
Idempotency
Anchor + email key + ledger
Safe weekly cron and backfill
v1 scope
Groww Play Store only
Reduce ingestion and config surface


20. Related Documents

- [problemstatement.md](./problemstatement.md) — product intent, requirements, and non-goals
- [implementation-plan.md](./implementation-plan.md) — phase-wise build plan, exit criteria, validated live-data assumptions
- [edge-cases.md](./edge-cases.md) — clustering fallbacks, quote validation, and failure modes
