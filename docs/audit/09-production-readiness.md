# 09 — Production readiness

**Verdict: not ready for paying customers.** The engineering base is better than most at this
stage (clean layering, 548 green tests on Postgres, deploy guards, migration discipline). What
is missing is integrity of the core numbers, the self-serve basics, and any live proof of the
integrations.

## Scorecard

| Area | State | Blocking items |
|---|---|---|
| Inventory correctness | 🔴 | C2, C3, C4, C5, C6 |
| Self-serve product | 🔴 | P1 recipes, P2 waste, P3 item edit, P4 password reset, P5 staff |
| Security | 🟠 | S1, S2, S3, S6, S7, S9, S10 |
| Tenant isolation | 🟢 app layer / 🟠 enforcement | S8 |
| Billing | 🟠 | Code tested on recorded payloads; never run in Stripe test mode; two tiers vs one-plan strategy (D1) |
| Square | 🟠 | Never run against sandbox; outbox missing |
| Email | 🔴 | Nothing has ever been delivered; no provider |
| AI extraction | 🟠 | Never run on a real invoice or label |
| Database / migrations | 🟢 | Postgres verified in this audit |
| Deployment | 🟠 | Image + compose proven locally; no host, no backups, no restore drill |
| Observability | 🟠 | Sentry wired but unset; no uptime check; plain logs |
| Source control / CI | 🔴 | No remote, no CI |
| Tests | 🟠 | Not hermetic (T1); key integrity scenarios untested |
| Docs | 🟠 | README/NEEDS-YOU overstate state |

## Launch readiness checklist

Tick only with evidence (test name, run output, screenshot, or log line).

### Integrity
- [ ] Ledger in place; nightly invariant `sum(movements) = qty_base` reports zero drift (B09–B10)
- [ ] Derived stock matches a hand calculation across a week with daily deliveries (B12)
- [ ] Concurrent invoice apply raises stock once, on Postgres (B05)
- [ ] Square outage during receipt: retried and lands; banner shown meanwhile (B11)
- [ ] Waste recorded with reason, cost and actor (B15)
- [ ] No code path deletes or overwrites inventory history (grep check in CI)

### Security
- [ ] Login throttled; password reset, change, email verification live (B16)
- [ ] Session rotated on login; sessions revocable (B16)
- [ ] CSRF tokens on all POST forms (B16)
- [ ] Security headers: CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy (B16)
- [ ] Invoice images served with server-chosen type (B07)
- [ ] Parametrised cross-tenant test over every id-taking route (B17)
- [ ] Square OAuth requests product scopes only (B20)
- [ ] One active tenant per Square merchant (B20)
- [ ] AI spend cap per tenant (B25)
- [ ] `pip-audit` clean in CI (B03)
- [ ] Production secrets generated fresh; `ENCRYPTION_KEY` stored in a password manager

### Product
- [ ] Recipe builder (B13) · item edit (B14) · staff invites + roles (B17) · org settings (B18)
- [ ] CSV export and deletion request (B19)
- [ ] Onboarding checklist; a stranger reaches first delivery received in < 45 min (B21)
- [ ] Pricing page, Stripe products and entitlements all say the same single plan (B22)
- [ ] Terms and privacy reflect retention decision (D10)

### Integrations (live)
- [ ] Square sandbox: connect, import, sale via webhook, delivery adjustment, missed-webhook recovery (B28)
- [ ] Stripe test mode: buy, fail payment, grace, cancel (B23)
- [ ] Alert digest and weekly report delivered to Gmail and Outlook inboxes, SPF/DKIM/DMARC pass (B24)
- [ ] 3–5 real invoices and 12 real labels scored with the existing harnesses (`NEEDS-YOU.md` §4)

### Operations
- [ ] Private git remote; CI green on SQLite + Postgres (B01, B03)
- [ ] Staging and production on the host; `/readyz` monitored externally (B26)
- [ ] Automated daily backups + PITR confirmed; restore drill completed and timed (B27)
- [ ] Sentry receiving errors from web and scheduler
- [ ] Runbook: deploy, rollback, restore, rotate a secret, reconnect a merchant
- [ ] Support inbox monitored; response-time promise on the site

### Go / no-go
- [ ] All boxes above ticked, or each open one has a written acceptance from you
- [ ] First pilot customer agreed, with the $149 setup or self-serve
