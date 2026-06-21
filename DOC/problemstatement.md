# Weekly Product Review Pulse — Problem Statement

## Overview

We are building an automated weekly **pulse** that turns public **Google Play** reviews for **Groww** into a one-page insight report and delivers it to stakeholders through Google Workspace.

Delivery must use **MCP (Model Context Protocol)** so writes to Google Docs and Gmail go through dedicated MCP servers—not ad hoc API calls inside the agent. **The Google Docs and Gmail MCP servers are built and provided in this project**, so the pulse agent can connect to them directly from the same repository.

**Initial scope:** Groww only (Google Play reviews).

---

## Objective

Give product, support, and leadership teams a repeatable, weekly snapshot of what Groww customers are saying in Play Store reviews: themes, representative quotes, and actionable ideas—without manual copy-paste or one-off spreadsheets.

---

## What the System Does

1. **Ingest** public Google Play reviews for Groww from the last 8–12 weeks (configurable window), using a scraper-based Play Store ingestion module.

2. **Cluster and rank** feedback using embeddings and density-based clustering (e.g. UMAP + HDBSCAN), then use an LLM to:
   - Name themes
   - Pull verbatim quotes
   - Propose action ideas
   - Validate that quotes appear in real review text

3. **Render** a concise one-page narrative:
   - Top themes
   - Quotes
   - Action ideas
   - Short “who this helps” section

4. **Deliver** outputs only through Google Workspace MCP servers **provided in this project**:
   - **Google Docs MCP** — append each week’s report as a new dated section to a single running document (*Weekly Review Pulse — Groww*). The Doc is the system of record and preserves history.
   - **Gmail MCP** — send a short stakeholder email with a deep link to the new section in that Doc (heading link), not a duplicate full report in email alone.

### Architecture (modular concerns)

| Concern | Where it lives |
|--------|----------------|
| Data retrieval | Play Store ingestion module (Groww) |
| Reasoning | Clustering + LLM summarization (themes, quotes, actions) |
| Output generation | Report + email rendering (structured for Docs and HTML/text for Gmail) |
| Human-visible delivery | MCP tools only → Google Docs MCP + Gmail MCP (both in this project) |

The agent is an **MCP host/client**; it does not embed Google credentials or call the Docs/Gmail REST APIs directly for delivery. Google OAuth and API access are configured in the MCP servers shipped with this project.

---

## Key Requirements

- **MCP-based delivery:** Append to the shared Google Doc and send Gmail only via the project’s MCP servers’ tools (e.g. document batch update, draft/create/send flows as defined in architecture).
- **Weekly cadence:** Designed to run once per week for Groww (e.g. scheduled job Monday morning IST), with a CLI for backfill of any ISO week.
- **Idempotent runs:** Re-running the same ISO week must not create duplicate Doc sections or duplicate sends. Enforced with a stable section anchor in the Doc and a run-scoped idempotency check on email (see architecture).
- **Auditable:** Each run records delivery identifiers (e.g. doc heading / message ids) and enough metadata to answer “what was sent when, for which week?”
- **Safety and quality:** PII scrubbing on review text before LLM and before publishing; reviews treated as data, not instructions; cost/token limits per run.

---

## Non-Goals (Explicit)

- Additional fintech products beyond Groww (e.g. INDMoney, PowerUp Money, Wealth Monitor, Kuvera) in the initial scope.
- Apple App Store or other review sources in the initial scope.
- A generic Google Workspace product beyond what the pulse needs (Docs append + Gmail send/draft).
- Real-time streaming analytics or a BI dashboard (the running Google Doc is the living artifact).
- Social sources (Twitter, Reddit, etc.) in the initial scope.
- Storing Google OAuth secrets in the agent codebase—they belong in the MCP servers’ configuration within this project.

---

## Who This Helps

| Audience | Value |
|----------|-------|
| **Product** | Prioritize roadmap from recurring themes |
| **Support** | Spot repeating complaints and quality issues |
| **Leadership** | Fast health snapshot tied to customer voice |

---

## Sample Output (Illustrative)

**Groww — Weekly Review Pulse**  
**Period:** Last 8–12 weeks (rolling window)  
**Source:** Google Play reviews

### Top themes

- **App performance & bugs** — Lag, crashes during trading hours; login/session timeouts.
- **Customer support friction** — Slow responses; unresolved tickets.
- **UX & feature gaps** — Confusing navigation for portfolio insights; missing advanced analytics.

### Real user quotes

- “The app freezes exactly when the market opens, very frustrating.”
- “Support takes days to reply and doesn’t solve the issue.”
- “Good for beginners but lacks detailed analysis tools.”

### Action ideas

- **Stabilize peak-time performance** — Scale infra during market hours; improve crash visibility.
- **Improve support SLA visibility** — Expected response time in-app; ticket status tracking.
- **Enhance power-user features** — Advanced portfolio analytics; clearer investments navigation.

---

## What This Solves

Same intent as today: roadmap alignment for product, issue clustering for support, and a leadership-friendly snapshot—now **automated**, **archived in Google Docs**, and **announced by email** with a link back to the canonical section.

---

## Delivery Expectations (Stakeholder-Facing)

- Each run adds one clearly labeled section to Groww’s pulse Google Doc (dated / week-labeled).
- The email is a brief teaser (e.g. top themes as bullets) plus a “Read full report” link to that section.
- Development/staging may default to draft-only email until explicit confirmation to send, per implementation plan.
