# Phase 8 — Stakeholder Sign-Off Checklist

Complete after one successful **staging** run (`pulse run --email-mode draft`) and before Phase 9 production rollout.

**Sample week:** _______________  
**Run id:** _______________  
**Reviewer:** _______________  
**Date:** _______________

---

## Report quality

| # | Criterion | Pass | Notes |
|---|-----------|------|-------|
| 1 | Top themes reflect real Play Store feedback (not generic noise) | ☐ | |
| 2 | Theme summaries are actionable and concise | ☐ | |
| 3 | User quotes are verbatim from reviews (no hallucination) | ☐ | |
| 4 | Action ideas are practical for Product / Support | ☐ | |
| 5 | No raw PII (emails, phone numbers, IDs) in Doc or email | ☐ | |
| 6 | Report length is appropriate for weekly scan (~5 min read) | ☐ | |

---

## Delivery

| # | Criterion | Pass | Notes |
|---|-----------|------|-------|
| 7 | Doc section heading matches `Groww — Weekly Review Pulse — {iso_week}` | ☐ | |
| 8 | Email subject and bullets match Doc themes | ☐ | |
| 9 | Doc deep link in email opens the correct staging Doc | ☐ | |
| 10 | Re-running same week does not duplicate Doc or email | ☐ | |
| 11 | `pulse status` shows doc + email delivery ids | ☐ | |

---

## Approvals

| Role | Name | Approved | Date |
|------|------|----------|------|
| Product | | ☐ | |
| Support | | ☐ | |
| Leadership | | ☐ | |

---

## Sign-off

- [ ] Staging E2E runbook completed ([staging-e2e.md](./staging-e2e.md))
- [ ] Quality gate passed: `pulse quality-gate --product groww --iso-week …`
- [ ] Approved to proceed to **Phase 9 — Production Rollout** (scheduler + `send` mode)

**Signed:** _________________________ **Date:** _____________
