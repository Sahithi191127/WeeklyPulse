# Weekly Product Review Pulse — Edge Cases

This document defines expected behavior for boundary conditions, fallbacks, and failure modes. It supplements [Architecture.md](./Architecture.md) and [implementation-plan.md](./implementation-plan.md).

---

## 1. Ingestion

| Edge case | Detection | Behavior |
|-----------|-----------|----------|
| Play Store scrape HTTP error (4xx/5xx) | Non-2xx response after retries | Abort run; ledger `failed`; no Doc/email |
| Rate limit / CAPTCHA / block | 429, empty pages, or block page HTML | Exponential backoff (max 3); abort if exhausted |
| Zero raw reviews in window | Empty scrape result | Abort; log `review_count: 0` |
| Package id not found | 404 or missing listing | Fail fast with config error; do not retry |
| Pagination ends early | No next page token | Stop pagination; proceed with collected reviews |
| Duplicate reviews across pages | Same hash `(text, rating, published_at)` | Dedupe before normalization; keep first seen |
| Review outside date window | `published_at` before window start | Exclude from raw set before normalize |
| Stale cache on retry same day | Cache exists for `data/cache/{product}/{date}/` | Reuse cache unless `--force-refresh` |
| Force refresh mid-backfill | Operator passes refresh flag | Re-scrape; overwrite cache for that date |
| Network timeout mid-pagination | Request timeout | Retry page; if partial cache written, manifest marks `incomplete` and re-run resumes or restarts scrape |

**Rule:** Ingestion never writes to Google Workspace. Any ingestion failure aborts before embedding.

---

## 2. Normalization & Quality Filters

| Edge case | Detection | Behavior |
|-----------|-----------|----------|
| Review &lt; 8 words | Word count after trim | Drop; do not include in normalized set |
| Non-English text | Language detector ≠ `en` (config `allowed_language`) | Drop |
| Emoji-only or emoji-heavy | Emoji regex / ratio threshold | Drop |
| Empty text after strip | `text == ""` | Drop |
| Rating missing or invalid | Not in 1–5 | Drop raw review; log warning |
| Normalized count &lt; `min_reviews` (20) | Post-normalize count | Abort pipeline before embedding; ledger `failed` |
| Normalized count &gt; `max_reviews` (5000) | Post-normalize count | Truncate to most recent by `published_at` when field exists; else random sample with fixed seed 42 |
| Very long review (&gt; 10k chars) | Char length | Keep for normalize; truncate at embed/scrub stage via `max_review_chars` |
| Identical text, different ratings | Distinct hashes | Keep both (rating is part of hash) |
| Hinglish written in Latin script | Language detector may misclassify | v1: English-only filter may drop valid Hinglish reviews; accept as known limitation |

**Typical Groww ratio:** ~800–900 normalized from ~5,000 raw (~17%). Alert if normalized &lt; 100 without scrape error (possible listing or filter regression).

---

## 3. Clustering Fallbacks

Primary path: UMAP (`random_state=42`) → HDBSCAN (`min_cluster_size=5`) → rank by `score = size × (6 − avg_rating)` → top `max_themes` (3–5).

### 3.1 All noise (every label = −1)

1. Log `clustering_fallback: lower_min_cluster_size`.
2. Re-run HDBSCAN once with `min_cluster_size = max(3, floor(n_reviews × 0.01))`.
3. If still all noise:
   - **Option A (default):** Abort run; ledger `failed`; message: insufficient cluster structure.
   - **Option B (config `clustering.fallback_rating_stratify: true`):** Skip clustering; run single LLM pass on stratified samples (equal draws from 1–2★, 3★, 4–5★ buckets, up to `max_samples_per_cluster × 3`). Report themes labeled as rating-stratified (metadata flag on run).

### 3.2 One dominant cluster (&gt; 80% of reviews)

1. Log `clustering_fallback: rating_split`.
2. Split reviews into buckets: `low` (1–2★), `mid` (3★), `high` (4–5★).
3. Re-embed and cluster each non-empty bucket independently (or treat buckets as pseudo-clusters if bucket size &lt; `min_cluster_size`).
4. Re-rank combined cluster list by score; take top `max_themes`.

Config: `clustering.dominant_cluster_threshold: 0.8` (enable/disable split).

### 3.3 Many micro-clusters (count &gt; 3 × max_themes)

- Do not merge clusters in v1.
- Take top `max_themes` by score only; log dropped cluster ids and sizes.

### 3.4 Fewer than `max_themes` valid clusters

- Summarize only clusters that exist (e.g. 2 themes if only 2 clusters).
- Do not pad with noise-cluster reviews unless noise volume &gt; `clustering.noise_theme_threshold` (default: 15% of corpus).

### 3.5 Noise cluster volume high but not labeled as cluster

- If noise reviews &gt; `noise_theme_threshold` × total: optional single “Other / mixed feedback” LLM pass on noise sample (config `clustering.summarize_noise: false` by default in v1).

### 3.6 Embedding failure

- **Local (BGE-small):** model load or encode errors abort the run; no partial Doc write.
- **OpenAI (optional):** retry batch with exponential backoff (max 3); abort on persistent failure.

### 3.7 UMAP/HDBSCAN numerical edge cases

- If UMAP fails (e.g. n_neighbors ≥ n_samples): reduce `n_neighbors` to `min(15, n_samples - 1)` once; abort if still failing.

---

## 4. PII Scrubbing

| Edge case | Behavior |
|-----------|----------|
| Email in review | Replace with `[EMAIL]` |
| Indian mobile (+91, 10-digit) | Replace with `[PHONE]` |
| Long numeric sequences (10–12 digits, PAN/Aadhaar-like) | Replace with `[ID]` |
| URL with query tokens | Redact path/query; keep domain if harmless |
| Financial amounts (`10k`, `₹`, `lakhs`) | **Keep** in v1 — theme signal |
| Review empty after scrub | Drop review from embed/cluster; decrement count; re-check `min_reviews` |
| PII only review (“call me 98…” → all redacted) | Drop if &lt; 8 words after scrub |
| Scrubbed text differs from raw | Quote validation uses **scrubbed** text only; Doc publishes scrubbed quotes |

---

## 5. LLM Summarization (Groq)

| Edge case | Behavior |
|-----------|----------|
| Invalid JSON response | Retry once same cluster; omit theme on second failure |
| Empty `theme_name` or `summary` | Treat as invalid; retry once; omit theme |
| Groq 429 / 529 | Exponential backoff, max 3 per request |
| Daily token budget exceeded mid-run | Stop remaining cluster calls; publish partial report if ≥1 valid theme; else abort |
| Run token estimate &gt; `max_tokens_per_run` | Drop longest review samples from prompts until under budget |
| Prompt injection in review (“ignore previous instructions”) | Untrusted-data framing; no tool execution; reviews never in system role |
| All clusters fail LLM | Abort; no Doc/email |
| Re-prompt after quote failure | Max 1 re-prompt per cluster; counts toward RPM/RPD |
| Sequential rate limit | Enforce `request_interval_seconds ≥ 2` between Groq calls |

**Minimum viable report:** At least **1** theme with validated quotes and ≥1 action idea before delivery. If only themes without quotes remain after validation, abort or deliver with explicit “no validated quotes” banner (config `delivery.allow_quoteless_themes: false` default).

---

## 6. Quote Validation

Validation order for each LLM-produced quote:

1. Normalize whitespace (collapse runs, trim).
2. Normalize punctuation lightly (curly quotes → straight; optional strip trailing `.`).
3. **Case-insensitive substring match** against scrubbed reviews in the **same cluster**.
4. If no match in cluster → fallback match against **full scrubbed corpus** for that run.
5. If quote ends with `...` or `…` → accept **prefix match** (quote stem length ≥ 20 chars or ≥ 50% of shortest candidate).

| Edge case | Behavior |
|-----------|----------|
| Quote matches after PII redaction | Valid if substring of scrubbed source |
| Quote uses different ellipsis character | Accept both `...` and `…` |
| Minor typo in quote vs source | v1: **no** fuzzy match; quote dropped |
| Hinglish in quote, English detector passed review | Case-insensitive match only |
| LLM paraphrases instead of quoting | Fails validation; dropped |
| LLM adds surrounding quotation marks | Strip outer quotes before match |
| Multiple quotes, some invalid | Keep valid only; log dropped |
| All quotes invalid for theme | Re-prompt cluster once; omit theme if still invalid |
| Duplicate quotes across themes | Allow in v1; optional dedupe in renderer |

---

## 7. Output Generation

| Edge case | Behavior |
|-----------|----------|
| Zero themes after pipeline | Abort; no delivery |
| Theme count &lt; 3 | Deliver with available themes; email teaser lists all |
| Special characters in theme names | Escape for Docs API batchUpdate; HTML-encode for email |
| Very long action detail | Truncate at `max_output_tokens_per_theme` budget (renderer level) |
| ISO week boundary (run at year edge) | Anchor uses explicit `--iso-week`; heading shows `YYYY-Www` |
| Clock skew / timezone | Generated timestamp in IST; store UTC in ledger |

---

## 8. MCP Delivery & Idempotency

### 8.1 Google Docs MCP

| Edge case | Behavior |
|-----------|----------|
| Section anchor already exists | `find_section_by_anchor` returns existing `heading_id`; skip `append_section` |
| Doc id invalid or no access | Fail fast; ledger `failed`; no email attempt if orchestrator orders Doc first |
| `append_section` succeeds, URL resolution fails | Store `heading_id`; email uses `get_document_url` retry |
| Concurrent runs same iso_week | Ledger unique constraint + Doc anchor; second run no-ops or waits (single-worker scheduler assumed) |
| Doc API transient 5xx | Retry batchUpdate max 3 |

### 8.2 Gmail MCP

| Edge case | Behavior |
|-----------|----------|
| `check_idempotency` → already sent | Skip `send_email`; return prior `message_id` |
| Staging `default_mode: draft` | Always `create_draft` unless `--email-mode send` |
| Draft created, operator deletes draft manually | Idempotency key still set; retry may skip — document in runbook |
| Invalid recipient | Fail fast; partial delivery if Doc already written |
| HTML + plain text mismatch | Both provided; clients choose; link must appear in both |

**Idempotency key:** `{product}-{iso_week}-email` (e.g. `groww-2026-W23-email`).

### 8.3 Orchestrator ordering

Default: **Doc first, then Gmail** — so email always has a resolvable deep link.

---

## 9. Partial Failure & Recovery

| Failure point | Doc | Email | Ledger | Retry behavior |
|---------------|-----|-------|--------|----------------|
| Ingestion | — | — | `failed` | Safe full retry |
| Pipeline / LLM | — | — | `failed` | Safe full retry |
| Doc append fails | — | — | `failed` | Safe full retry |
| Doc OK, Gmail fails | ✓ | — | `failed` + partial delivery ids | Retry: Doc no-op, Gmail retried |
| Gmail OK, ledger write fails | ✓ | ✓ | missing / inconsistent | **Critical log**; MCP idempotency prevents duplicate email |
| Ledger says `in_progress` stale | — | — | stuck | Operator `pulse status`; manual mark failed or resume after timeout policy |

**Stale `in_progress`:** If `started_at` &gt; 2 hours ago and no `completed_at`, allow new run after logging warning (configurable).

---

## 10. CLI & Scheduling

| Edge case | Behavior |
|-----------|----------|
| `pulse run` without `--iso-week` on Monday 09:00 IST | Use configured default week policy (current vs previous complete week) |
| Backfill range includes completed weeks | Skip completed; log skipped ids |
| Backfill hits failed week | Retry failed weeks; idempotency prevents duplicate delivery on success |
| `dry-run` | Full pipeline + render; no MCP calls; optional write artifact JSON |
| Missing `GROQ_API_KEY` | Fail at summarizer with clear error |
| Missing OpenAI key (only if `embedding.provider: openai`) | Fail at embed step |

---

## 11. Security & Abuse

| Edge case | Behavior |
|-----------|----------|
| Review contains credential-like strings | Scrubbed before LLM and publish |
| OAuth token expired (MCP) | MCP server refresh; fail fast if refresh fails |
| Google API quota exceeded | Retry transient; fail with alert if persistent |
| Scrape blocked by IP | Abort; alert ops; use cache if valid same-day cache exists and policy allows |

---

## 12. Observability Expectations

Every fallback or dropped item should emit structured log fields:

```json
{
  "run_id": "groww-2026-W23-abc123",
  "stage": "clustering",
  "event": "clustering_fallback",
  "fallback_type": "lower_min_cluster_size",
  "detail": { "min_cluster_size": 3 }
}
```

Quote drops:

```json
{
  "stage": "quote_validation",
  "event": "quote_dropped",
  "theme": "App performance & bugs",
  "reason": "no_substring_match"
}
```

---

## 13. v1 Known Limitations

- Hinglish reviews may be filtered by English-only normalization.
- No fuzzy quote matching (typos in LLM output fail validation).
- Single product (Groww); single source (Play Store).
- No automatic merge of duplicate themes across weeks.
- Noise cluster themes off by default.

---

## Related Documents

- [Architecture.md](./Architecture.md) — §7.2 clustering fallbacks reference this doc
- [implementation-plan.md](./implementation-plan.md) — Phase 2 and Phase 8 quality gates
- [problemstatement.md](./problemstatement.md) — safety and idempotency requirements
