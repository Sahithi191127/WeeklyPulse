# Hosted Google Workspace MCP — API Contract

**Source repo:** [github.com/Sahithi191127/MCPServer](https://github.com/Sahithi191127/MCPServer)  
**Deployed URL:** `https://web-production-c5ea8.up.railway.app`  
**Runtime:** FastAPI (REST). WeeklyPulse calls HTTP JSON endpoints — not local stdio MCP.

OpenAPI: `{base_url}/openapi.json` · Interactive docs: `{base_url}/docs`

See the [MCPServer README](https://github.com/Sahithi191127/MCPServer/blob/main/README.md) for Google Cloud OAuth setup (`credentials.json` / `token.json` on the **server** only).
---

## Pulse agent configuration (WeeklyPulse)

| Source | Key | Purpose |
|--------|-----|---------|
| `config/mcp/servers.json` | `mcpServers.google-workspace.url` | Default MCP base URL |
| `.env` / shell | `MCP_SERVER_URL` | Override base URL |
| `.env` / shell | `MCP_API_KEY` | Sent as `X-API-Key` when Railway has `API_KEY` set |

## MCP server configuration (MCPServer repo / Railway)

Configured on the **deployed server**, not in WeeklyPulse:

| Server env | Purpose |
|------------|---------|
| `GOOGLE_CREDENTIALS_JSON` | OAuth client secrets (Railway) |
| `GOOGLE_TOKEN_JSON` | Saved OAuth token (Railway) |
| `API_KEY` | When set, requests must include matching `X-API-Key` |
| `REQUIRE_APPROVAL` | `false` on Railway (required for automated pulse runs) |

When `REQUIRE_APPROVAL=true` and no TTY (e.g. Railway without override), append/draft calls return **403**. Production must set `REQUIRE_APPROVAL=false` and use `API_KEY` if auth is needed — see [server.py](https://github.com/Sahithi191127/MCPServer/blob/main/server.py).
---

## Endpoint mapping (Architecture §9.1 → hosted REST)

| Architecture tool | Hosted REST | Notes |
|-------------------|-------------|-------|
| Health / connectivity | `GET /health` | Returns `{ status, service, config }` |
| `append_section` | `POST /append_to_doc` | Body: `{ doc_id, content }` — plain text from `DocSection.content` |
| `get_document_url` | *(client-built)* | `https://docs.google.com/document/d/{doc_id}/edit` |
| `find_section_by_anchor` | *(not on server v1)* | Pulse uses local ledger `data/deliveries/docs/{doc_id}/{anchor}.json` until server adds lookup |

### `POST /append_to_doc`

**Request**

```json
{
  "doc_id": "GOOGLE_DOC_ID",
  "content": "Groww — Weekly Review Pulse — 2026-W24\n\n..."
}
```

**Headers:** `Content-Type: application/json`, optional `X-API-Key` (required when server `API_KEY` is set)

**Response (success):**

```json
{
  "status": "success",
  "result": { }
}
```

### `GET /health`

**Response (example)**

```json
{
  "status": "ok",
  "service": "google-mcp-server",
  "runtime": "fastapi",
  "config": {
    "has_google_token": true,
    "has_google_credentials": true
  }
}
```

### `GET /`

Service index — lists available endpoints.

---

## Idempotency (Phase 4 — Docs)

1. Before append, pulse checks `data/deliveries/docs/{doc_id}/{anchor}.json`.
2. If record exists → skip append, return existing URL (`--force` overrides).
3. After successful append → write local delivery record.
4. Phase 6 run ledger will complement this for orchestrated weekly runs.

---

## Gmail endpoint mapping (Architecture §9.2 → hosted REST)

| Architecture tool | Hosted REST | Notes |
|-------------------|-------------|-------|
| `check_idempotency` | *(local ledger v1)* | `data/deliveries/email/{idempotency_key}.json` |
| `create_draft` | `POST /create_email_draft` | Body: `{ to, subject, body }` — `body` is plain text from `EmailTeaser.text_body` |
| `send_email` | `POST /send_email` | Same body as draft; pulse agent calls when `PULSE_EMAIL_MODE=send` (requires MCPServer deploy) |

### `POST /create_email_draft`

**Request**

```json
{
  "to": "product-leads@example.com, support-leads@example.com",
  "subject": "Groww Weekly Review Pulse — 2026-W24",
  "body": "Top themes this week:\n\n• ..."
}
```

**Response (success):**

```json
{
  "status": "success",
  "result": {
    "id": "DRAFT_ID",
    "message": { }
  }
}
```

## Idempotency (Phase 5 — Email)

1. Key format: `{product}-{iso_week}-email` (e.g. `groww-2026-W24-email`).
2. Before draft, pulse checks `data/deliveries/email/{idempotency_key}.json`.
3. If record exists → skip draft (`--force` overrides).
4. Doc deep link injected from Phase 4 ledger URL when available.

---

## CLI

```bash
# Connectivity
pulse mcp health
pulse config validate --secrets

# Doc delivery (Phase 4)
pulse deliver-doc --product groww --doc-section-json tests/fixtures/expected_doc_section.json
pulse deliver-doc --product groww --force

# Email draft (Phase 5)
pulse deliver-email --product groww --email-teaser-json tests/fixtures/expected_email_teaser.json
pulse deliver-email --product groww --to you@example.com
pulse deliver-email --product groww --force
```
