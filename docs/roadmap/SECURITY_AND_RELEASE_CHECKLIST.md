# Security and release checklist

Phase 3, 2026-11-05. Planning only. Every item has a **testable pass condition** and the task that
delivers it. Baseline: [NON_FUNCTIONAL_REQUIREMENTS](../product/NON_FUNCTIONAL_REQUIREMENTS.md)
(SEC, REL, OBS, NFR §8) and the [security findings](../audit/05-security.md) S1–S13. Used at G-R1
(T4.17) and, from then on, for every release.

## 1. Security controls

| # | Control | Pass condition | Task | Finding |
|---|---|---|---|---|
| S-01 | Tenant isolation verified | Generated isolation suite: 100 % of id and file routes return 404/403 cross-tenant (AC-SAAS-01) | T1.02, T1.03 | S8 |
| S-02 | Authorization enforced | Every route declares a permission (static test); permission matrix passes (AC-STF-02); location scope enforced (AC-STF-08) | T1.06 | — |
| S-03 | Authentication hardened | Throttling (AC-SAAS-03), verification gates (AC-SAAS-02), single-use expiring reset (AC-SAAS-04), revocable sessions (AC-STF-04), CSRF on every state-changing form, POST logout, optional TOTP | T1.07–T1.11 | S1, S2, S4, S5 |
| S-04 | Secrets protected | No secret in the repository (secret scan in CI); production secrets only in host environment; separate test/live keys; rotation procedure written | T0.03, T0.09, T4.09 | S13 |
| S-05 | POS credentials secured | Tokens Fernet-encrypted with a key outside the DB; product-only scopes; revoked on disconnect and deletion; one active connection per merchant + location (AC-POS-11) | T3.02 | S6, S7 |
| S-06 | Webhooks validated | Square raw-body HMAC and Stripe signature verified; invalid signature rejected with no side effect (AC-POS-10); replay de-duplicated by event id | T3.06, existing billing | — |
| S-07 | Sensitive data not in logs | Log-capture test: no bodies, cookies, tokens, passwords or emails in logs; Sentry scrubbing test (existing) | T0.10 | — |
| S-08 | XSS and file uploads | Images served with allow-listed type + `nosniff` + `Content-Disposition`; content sniffing and size limit on upload; autoescaping kept | T0.06 | S3, S12 |
| S-09 | Security headers | CSP, HSTS (https), frame-ancestors, referrer policy, nosniff on every response (test) | T0.06 | S10 |
| S-10 | Settings not exposed to templates | No secret-bearing object in Jinja globals (test) | T0.06 | S11 |
| S-11 | Critical vulnerabilities resolved | `pip-audit` in CI with no unaddressed critical CVE; Dependabot active | T0.03 | O5 |
| S-12 | AI cost abuse prevented | Extraction only for verified users; per-tenant daily cap (AC-RCV-10) | T1.08, T2.11 | S9 |
| S-13 | Billing security | No card data handled; entitlements change only from verified billing events or audited admin action | T4.01, T4.02 | — |
| S-14 | Support access controlled | Separate admin auth with MFA; time-boxed, reasoned, audited impersonation visible to the tenant | T4.07 | — |
| S-15 | Least privilege | App DB role cannot UPDATE/DELETE ledger tables (once roles are split); OAuth scopes minimal | T1.16, T3.02 | — |
| S-16 | Transport | HTTPS only; HSTS; no plaintext endpoints (startup guard exists) | T0.09, T4.09 | — |
| S-17 | Rate limiting | Login, reset, invite, upload, extraction, export and webhook endpoints limited per IP and per account/org | T1.09, T2.11, T4.05 | — |
| S-18 | Data deletion works | Deletion leaves no tenant rows or files (Postgres test, AC-ADM-05) | T4.06 | — |

## 2. Release readiness (G-R1 — NFR §8 production readiness standard)

| # | Gate | Pass condition (evidence) | Task |
|---|---|---|---|
| R-01 | All R1 acceptance criteria pass | Traceability script lists no R1 AC without a test or manual check; CI green | T4.17 |
| R-02 | Tenant isolation suite passes | CI | T1.03 |
| R-03 | Ledger integrity | Idempotency, concurrency, reversal and nightly verification tests pass on Postgres | T1.16, T1.21 |
| R-04 | Square end to end | Recorded sandbox run (connect → catalog → orders → consumption → refund → disconnect); one real merchant pilot before Connected is sold widely | T3.11 |
| R-04a | Standalone end to end | CI job + one real Standalone pilot | T2.22, pilot |
| R-05 | Stripe lifecycle | Recorded test-mode run (AC-SAAS-05, -06) | T4.04 |
| R-06 | Email delivery | Verification, reset, invite and digest received in real inboxes; SPF/DKIM/DMARC pass | T4.09 |
| R-07 | Backups and restore | PITR enabled; restore drill log | T4.09 |
| R-08 | Monitoring and alerts | Test alert of each type reaches the founder's phone | T4.10 |
| R-09 | Security checks | §1 items S-01..S-18 evidenced | T4.17 |
| R-10 | Legal and support | Privacy policy, terms, support contact, sub-processor list live | T4.12 |
| R-11 | Onboarding | Non-founders complete both onboarding paths in < 45 min (AC-SAAS-09, -10) | T2.20, T3.10 |
| R-12 | Decisions closed | No R1-blocking decision open in PRODUCT_DECISIONS or IMPLEMENTATION_DECISIONS | T4.17 |
| R-13 | Accessibility | No serious axe violations on R1 screens; manual employee-workflow check | T4.13 |
| R-14 | Performance | PERF-01..07 met at design headroom or deviations accepted by the owner | T4.14 |

## 3. Per-release checklist (after G-R1)

- [ ] CI green (all jobs) on the release commit.
- [ ] Migrations reviewed: expand/contract respected; destructive steps approved; backup taken
      immediately before deploy.
- [ ] Release notes list behaviour changes visible to customers.
- [ ] Staging deployed and smoke-tested with the same migration.
- [ ] Rollback plan stated (previous image compatible with the new schema until contract).
- [ ] Monitoring watched for 30 minutes after deploy.
- [ ] Execution log entry written; task statuses updated.

## 4. Operational requirements

| Requirement | Pass condition | Task |
|---|---|---|
| Runbooks | Written procedures for: restore from backup, rotate a secret, revoke a POS connection, re-run a failed job, respond to `balance_drift`, handle a data-deletion request | T4.07, T4.09, T4.10 |
| Support recovery | Founder console can find a tenant, see sync and job health and re-run jobs without database access | T4.07 |
| Incident alerts | Thresholds per OBS-06 (sync failure > 2 h, dead letters > 0, drift > 0, error spike, backup failure) | T4.10 |
| Customer communication | Status/support contact published; in-app banner for degraded integrations (CMD-FR-08) | T3.09, T4.12 |
