# Non-Functional Requirements

Authoritative source for **security, reliability, performance, scalability, accessibility,
maintainability and observability**, and the **production readiness standard**. Multi-tenancy,
subscription lifecycle and data retention are specified in [SAAS_REQUIREMENTS](SAAS_REQUIREMENTS.md). Business data, supplier information,
employee records and sales information are protected data [CONFIRMED].

Scale assumption for sizing [ASSUMED A-03]: launch 21 organizations; design headroom 500. Per
organization ≤ 1,000 items, ≤ 1,500 POS order lines/day, ≤ 15 users.

## 1. Security

| ID | Area | Requirement |
|---|---|---|
| SEC-01 | Authentication | [SAAS_REQUIREMENTS §2](SAAS_REQUIREMENTS.md#2-authentication): verified email, throttled login, reset, MFA, session revocation. Passwords hashed with a memory-hard algorithm (argon2id/scrypt/bcrypt with current cost) |
| SEC-02 | Authorization | Every server operation checks membership + permission ([USER_ROLES_AND_PERMISSIONS](USER_ROLES_AND_PERMISSIONS.md)); deny by default; tested per permission key |
| SEC-03 | Tenant isolation | Central enforcement (SAAS-T-04); automated cross-tenant tests for every resource and file (SAAS-T-09) |
| SEC-04 | API security | Same authz on every endpoint; object ids not trusted without tenant check; no mass assignment (explicit input schemas) |
| SEC-05 | CSRF | Cookie-authenticated state-changing requests protected (token or SameSite=Lax/Strict + origin check) |
| SEC-06 | Input validation | Server-side schema validation for every input (types, ranges, lengths, units, enums); quantities > 0 where required; dates sane |
| SEC-07 | SQL injection | Parameterized queries / ORM only; no string-built SQL with user input |
| SEC-08 | XSS | Auto-escaping templates; no raw HTML from user/extracted data; Content-Security-Policy; uploaded files served with safe content types and `Content-Disposition` (existing-app stored-XSS finding S3) |
| SEC-09 | Security headers | HSTS, CSP, X-Content-Type-Options, Referrer-Policy, frame-ancestors |
| SEC-10 | Secrets | Environment/secret manager only; never in code or logs; separate test and live keys; rotation procedure documented; POS tokens encrypted at rest with a key outside the DB |
| SEC-11 | Webhooks | Signature verified on raw body (Square, Stripe); replay-safe via event id; reject on failure |
| SEC-12 | Rate limiting | Login, reset, invite, upload, extraction, export, public endpoints; per IP and per account/org |
| SEC-13 | File uploads | Allow-list types (JPEG, PNG, HEIC, PDF); size ≤ 15 MB; content sniffing, not extension; images re-encoded/stripped of metadata where practical; stored privately |
| SEC-14 | Audit logs | AuditEvent for settings, roles, memberships, connections, recipes, mappings, approvals, support access, billing (DR-23); append-only; retained ≥ 1 year |
| SEC-15 | Dependencies | Locked dependency versions; automated vulnerability scanning in CI; monthly update cadence; critical CVEs patched within 7 days |
| SEC-16 | Transport | TLS everywhere; no plaintext endpoints |
| SEC-17 | Least privilege | DB roles per process; cloud IAM scoped; OAuth scopes minimal (SQ-02) |
| SEC-18 | AI extraction | Only the tenant's own document sent; no secrets in prompts; output treated as untrusted input |
| SEC-19 | Admin/support | Separate admin authentication; impersonation logged and time-boxed (SAAS-T-10) |
| SEC-20 | Billing security | No card data stored or handled (hosted checkout/portal); Stripe webhooks verified; entitlement changes only from verified billing events or audited admin action |
| SEC-21 | Encryption | Encryption at rest for database, backups and files; integration tokens additionally encrypted at application level with a key held outside the database; key rotation procedure |

## 2. Reliability and data safety

| ID | Requirement |
|---|---|
| REL-01 | Availability target 99.5 % monthly for the web app (excludes announced maintenance) |
| REL-02 | Inventory operations are transactional: a business event posts all its movements or none |
| REL-03 | Idempotency on every inbound integration and every retried operation (DR-05, §5 of [INTEGRATION_REQUIREMENTS](INTEGRATION_REQUIREMENTS.md#5-idempotency-keys)) |
| REL-04 | Outbound side effects via outbox, never inside the user's request transaction before commit |
| REL-05 | Backups: automated daily + point-in-time recovery; **RPO ≤ 15 minutes, RTO ≤ 4 hours**; restore drill quarterly and before launch |
| REL-06 | Object storage versioning or replication for invoice files; retention aligned with data policy |
| REL-07 | Background jobs: single active scheduler (leader lock), at-least-once execution with idempotent handlers, job health visible; missed run alerts |
| REL-08 | Nightly ledger verification (DR-03) |
| REL-09 | Graceful degradation: POS or extraction outage never blocks receiving, waste, counts or production |
| REL-10 | Migrations: forward-only in production, tested on a copy of production-shaped data, non-destructive by default; destructive changes require owner approval |
| REL-11 | Error handling: no swallowed exceptions; user sees an actionable message; operator sees context (tenant, request id, entity) |

## 3. Performance

| ID | Requirement (p95, at design headroom) |
|---|---|
| PERF-01 | Page/server response for operational screens (waste, count, receive, item list) ≤ 800 ms |
| PERF-02 | Dashboard ≤ 1.5 s |
| PERF-03 | Reports over 90 days ≤ 5 s, or run as background export |
| PERF-04 | POS order completion → consumption posted ≤ 5 minutes (webhook path); ≤ 60 minutes worst case (reconciliation) |
| PERF-05 | Waste log from item selection to saved ≤ 3 taps, ≤ 2 s round-trip on 4G |
| PERF-06 | Invoice extraction result ≤ 60 s, off the request path, with progress state |
| PERF-07 | Webhook acknowledgement ≤ 2 s |

## 3a. Scalability [PROPOSED]

Sizing note [ASSUMED, from A-03 plus an assumed average of ~5 recipe lines per order line]: a busy
Connected location could post ~7,500 ledger movements a day (~2.7 million a year). At the 500-org
headroom that is on the order of a billion movements a year. The ledger is the table that grows;
everything below follows from that.

| ID | Requirement |
|---|---|
| SCL-01 | The design headroom in A-03 is reached without architectural change; PERF targets hold at that headroom, verified by the pre-launch load test ([acceptance criteria](PRODUCT_ACCEPTANCE_CRITERIA.md#non-functional)) |
| SCL-02 | The web tier holds no per-user state in process memory, so any instance can serve any request and instances can be added |
| SCL-03 | Heavy work runs off the request path in background jobs: POS ingestion, invoice extraction, exports, report snapshots, nightly ledger verification. Job queues are fair per tenant, so one tenant's backlog cannot starve the others |
| SCL-04 | Screens read balances from the maintained projection (DR-03) and history reports from snapshots, never by summing a tenant's whole ledger per request. Ledger storage is indexed by organization, location, item and time; partitioning or archival of old movements is decided in Phase 2 and must keep every movement retrievable (DR-02) |
| SCL-05 | Per-tenant limits (items, users, locations, extraction volume, API calls) are configuration in the entitlement set (E-02), so a large tenant is a plan question rather than an incident |
| SCL-06 | POS synchronization respects provider rate limits: per-connection queues, backoff and resumable backfills; a slow or throttled provider delays only that connection |
| SCL-07 | Location is a dimension of every location-scoped record and index from R1 (DR-35), so multi-location Expansion needs no data migration |

## 4. Accessibility and usability

| ID | Requirement |
|---|---|
| UX-01 | WCAG 2.1 AA for all screens; Employee workflows tested with keyboard and screen reader |
| UX-02 | Mobile-first for Employees workflows (375 px); tablet for counts and receiving; desktop for reports |
| UX-03 | Touch targets ≥ 44 px; works with gloves/wet hands assumptions (large controls on line screens) |
| UX-04 | Consistent terminology per [PRODUCT_GLOSSARY](PRODUCT_GLOSSARY.md); no implementation jargon in UI |
| UX-05 | Error messages say what happened and what to do ("Carrot is counted by weight. Add a conversion to use mL.") |
| UX-06 | Status indicators: colour + text/icon, never colour alone |
| UX-07 | Search everywhere items are chosen; recent and favourite items first |
| UX-08 | Units shown the way the business counts them (display units), not base units |
| UX-09 | The InventoryOS management interface is visually and by domain distinct from any customer-facing site (e.g. JuJu's) |

## 5. Maintainability

| ID | Requirement |
|---|---|
| MNT-01 | Modular monolith with domain boundaries per [DOMAIN_MODEL §2](DOMAIN_MODEL.md#2-context-map-module-ownership); business rules in pure, unit-testable functions separate from I/O and presentation |
| MNT-02 | Automated tests: unit (domain rules), integration (DB, ledger, concurrency, idempotency), tenant-isolation, contract tests for adapters with recorded payloads, end-to-end for R1 workflows |
| MNT-03 | Hermetic test suite (no dependence on developer environment; existing-app finding T1) |
| MNT-04 | CI on every push: tests (incl. production DB engine), type checks, lint, dependency scan |
| MNT-05 | Every acceptance criterion in [PRODUCT_ACCEPTANCE_CRITERIA](PRODUCT_ACCEPTANCE_CRITERIA.md) maps to at least one automated test or a documented manual check |
| MNT-06 | Configuration via environment; no hardcoded credentials, URLs or tenant ids |
| MNT-07 | Docs: this package + architecture decision records for Phase 2 choices |
| MNT-08 | A Standalone end-to-end suite runs every R1 workflow with no POS configured and no POS credentials present; a static check keeps provider-specific types inside adapters |

## 6. Observability

| ID | Requirement |
|---|---|
| OBS-01 | Structured JSON logs with request id, organization id, user id (no PII payloads) |
| OBS-02 | Error tracking with PII scrubbing; alert on new error types |
| OBS-03 | Health endpoints: liveness, readiness (DB), scheduler heartbeat |
| OBS-04 | Uptime monitor on public endpoints; alerts to the founder's phone |
| OBS-05 | Business-integrity metrics per tenant: last successful POS sync, unprocessed events, outbox backlog, ledger drift count, extraction usage, job failures |
| OBS-06 | Founder alerting thresholds: sync failure > 2 h, outbox dead letters > 0, drift > 0, error rate spike, backup failure |
| OBS-07 | Email delivery monitoring: bounces/complaints suppressed and visible |

## 7. Privacy and compliance

- Personal data held: staff names/emails, supplier contacts, (via POS) no end-customer PII required
  — InventoryOS does not ingest customer names/contacts from orders (data minimization).
- Privacy policy and terms before first paying customer (SAAS-D-06).
- Data export and deletion per [SAAS_REQUIREMENTS §6](SAAS_REQUIREMENTS.md#6-data-export-retention-and-deletion).
- Sub-processor list (host, DB, storage, email, Stripe, Square, extraction provider) published.

## 8. Production readiness standard

InventoryOS is **production-ready** only when every item holds. "Built" is not production-ready
(owner's standing distinction for the existing app).

| # | Gate | Evidence required |
|---|---|---|
| 1 | All R1 acceptance criteria pass | Test run report + manual checklist |
| 2 | Tenant isolation suite passes | CI |
| 3 | Ledger integrity: idempotency, concurrency, reversal, nightly verification | Automated tests incl. concurrent-post test |
| 4 | Square: sandbox end-to-end (connect → catalog → orders → consumption → refund → disconnect) and one real merchant pilot | Recorded run |
| 4a | Standalone: onboarding, daily sales, import, waste, production, counts and reports end-to-end with no POS configured, and one real Standalone pilot | Recorded run + CI |
| 5 | Stripe: test-mode lifecycle (checkout, renewal, failed payment, grace, cancel, resubscribe) | Recorded run |
| 6 | Email: verification, reset, invite, digest delivered to real inboxes; SPF/DKIM/DMARC pass | Screenshots/headers |
| 7 | Backups + a successful restore drill | Drill log |
| 8 | Monitoring and alerts reach the founder's phone | Test alert |
| 9 | Security: headers, CSRF, rate limits, upload validation, dependency scan clean of criticals | Scan output |
| 10 | Privacy policy, terms, support contact published | URLs |
| 11 | Onboarding completed by a non-founder in < 45 min | Observed session |
| 12 | Founder decisions blocking R1 closed in [PRODUCT_DECISIONS](PRODUCT_DECISIONS.md) | Decision log |

## 9. Risks: business data and POS credentials

| Risk | Impact | Mitigation |
|---|---|---|
| Cross-tenant data exposure | Loss of trust; legal exposure | Central isolation mechanism (SAAS-T-04), isolation test suite, location-scope checks |
| POS token theft | Attacker reads a merchant's catalog/orders, or writes inventory if write scope granted | Least-privilege scopes, application-level token encryption, key outside DB, revocation on disconnect, audit of connection changes |
| Webhook spoofing/replay | Fake sales consume stock | Signature verification on raw body, event-id dedupe, reconciliation against provider |
| Over-scoped OAuth | Larger blast radius; harder provider review | Request only scopes in use (SQ-02) |
| Uploaded files as attack vector | Stored XSS, malware hosting | Type allow-list, content sniffing, private storage, safe content headers |
| AI extraction misuse | Cost blow-up; data sent to a third party | Per-tenant caps, verified email, only the tenant's own document sent, sub-processor disclosed |
| Billing manipulation | Free access to paid capabilities | Entitlements derived only from verified billing events |
| Data loss | Customers lose operational history | PITR backups, restore drills, append-only ledger |
| Employee misuse within a tenant | Shrinkage hidden by edits | Append-only ledger, actor on every entry, approvals, audit history |
| Supplier/employee personal data | Privacy obligations | Minimization, export and deletion, privacy policy |
