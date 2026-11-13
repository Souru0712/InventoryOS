# Architecture decision records

Technical decisions for InventoryOS, one file each. Product decisions live in
[PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md) as `C-nn`; these records cover how the product
is built ([ADR-0020](0020-architecture-decision-records.md)). Where they disagree,
PRODUCT_DECISIONS wins until one of them is corrected.

## When to write one

- An implementation decision `ID-nn` in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md)
  is approved: one ADR per ID (0001–0023 cover the 23 approved on 2026-11-05).
- A later change to architecture: a new component, replacing one, a new dependency, a change to a
  cross-cutting rule (security, logging, data access). Replacing a component the
  [reuse matrix](../audit/REUSE_ADAPT_REBUILD_MATRIX.md) marks reusable needs evidence and the
  owner's approval.
- Not for a task's ordinary implementation choices; those go in the task's execution-log entry.

## How

1. Copy [the template](0000-template.md) to `NNNN-short-title.md` with the next free number.
2. Status **Proposed** until the owner approves it; then **Accepted** with the date and the
   owner's words. A replaced decision becomes **Superseded by ADR-NNNN** and is never deleted.
3. Add it to the index below in the same change. `python scripts/check_docs.py` fails if an
   approved `ID-nn` has no ADR, if a file is missing from the index, or if a link is broken.

Task status and the record of work follow the roadmap's
[governance](../roadmap/README.md#4-governance): status lives only in the
[task index](../roadmap/DEVELOPMENT_TASK_BREAKDOWN.md), and each task adds an entry to
[the execution log](../EXECUTION-LOG.md).

## Index

| ADR | Title | Decision | Status |
|---|---|---|---|
| [ADR-0001](0001-modular-monolith-layout.md) | Application architecture and code layout | ID-01 | Accepted |
| [ADR-0002](0002-hosting-on-render.md) | Hosting | ID-02 | Accepted |
| [ADR-0003](0003-ci-on-github-actions.md) | CI | ID-03 | Accepted |
| [ADR-0004](0004-ruff-and-mypy.md) | Coding standards | ID-04 | Accepted |
| [ADR-0005](0005-uv-lockfile.md) | Dependency locking | ID-05 | Accepted |
| [ADR-0006](0006-postgres-jobs-table.md) | Background jobs | ID-06 | Accepted |
| [ADR-0007](0007-server-rendered-pages-with-htmx.md) | Frontend interactivity | ID-07 | Accepted |
| [ADR-0008](0008-playwright-browser-tests.md) | Browser end-to-end tests | ID-08 | Accepted |
| [ADR-0009](0009-keep-tenant-naming.md) | Physical naming | ID-09 | Accepted |
| [ADR-0010](0010-item-model-tables.md) | Item model tables (C-38) | ID-10 | Accepted |
| [ADR-0011](0011-resend-for-email.md) | Email provider | ID-11 | Accepted |
| [ADR-0012](0012-s3-object-storage.md) | Object storage | ID-12 | Superseded by ADR-0024 |
| [ADR-0013](0013-pilot-policy.md) | Pilot policy (refines C-40) | ID-13 | Accepted |
| [ADR-0014](0014-approve-pending-product-recommendations.md) | Approve pending product recommendations | ID-14 | Accepted |
| [ADR-0015](0015-defer-multi-location-pricing.md) | Multi-location pricing (PD-33) | ID-15 | Accepted |
| [ADR-0016](0016-snack-box-own-repository.md) | Snack-box extraction target (C-37) | ID-16 | Accepted |
| [ADR-0017](0017-juju-reference-tenant.md) | JuJu validation approach (C-42) | ID-17 | Accepted |
| [ADR-0018](0018-postgres-authoritative-tests.md) | Test database policy | ID-18 | Accepted |
| [ADR-0019](0019-project-claude-md.md) | Project `CLAUDE.md` | ID-19 | Accepted |
| [ADR-0020](0020-architecture-decision-records.md) | Architecture decision records | ID-20 | Accepted |
| [ADR-0021](0021-task-tracking-in-the-repository.md) | Task tracking | ID-21 | Accepted |
| [ADR-0022](0022-row-level-security-in-r1-1.md) | Postgres RLS timing | ID-22 | Accepted |
| [ADR-0023](0023-approved-new-dependencies.md) | New runtime dependencies | ID-23 | Accepted |
| [ADR-0024](0024-invoice-photos-in-postgres.md) | Invoice photos kept briefly, in Postgres | C-48 | Accepted |
| [ADR-0025](0025-schema-integrity-conventions.md) | Schema integrity conventions: UTC instants, delete rules, named CHECKs | T1.01 (DM-2) | Accepted |
| [ADR-0026](0026-scoped-data-access.md) | Scoped data access: OrgContext, scoped helpers, named system lookups | C-33 (T1.02) | Accepted |
| [ADR-0027](0027-locations-and-business-days.md) | Owner-managed locations, archiving, and business days | T1.04 (DM-3) | Accepted |
| [ADR-0028](0028-memberships-and-the-active-business.md) | Memberships and the business a request acts for | T1.05 (DM-4) | Accepted |
| [ADR-0029](0029-permission-keys-and-role-presets.md) | Permission keys, role presets, and what a role sees | T1.06 | Accepted |
| [ADR-0030](0030-server-side-sessions-and-csrf.md) | Server-side sessions, CSRF tokens, POST logout | T1.07 | Accepted |
| [ADR-0031](0031-email-verification-and-password-reset.md) | Email verification, password reset and change | T1.08 | Accepted |
| [ADR-0032](0032-throttling-sign-in-and-emailed-links.md) | Throttling sign-in and emailed links | T1.09 | Accepted |
| [ADR-0033](0033-invitations-and-team-management.md) | Invitations and team management | T1.10 | Accepted |
| [ADR-0034](0034-two-step-sign-in.md) | Two-step sign-in with an authenticator app | T1.11 | Accepted |
| [ADR-0035](0035-business-audit-history.md) | The business audit history | T1.12 | Accepted |
| [ADR-0036](0036-splitting-stock-items.md) | Splitting stock items into inventory items and item locations | T1.13 | Accepted |
| [ADR-0037](0037-reading-items-from-the-split-tables.md) | Reading stock items' definitions and settings from the split tables | T1.13 | Accepted |
| [ADR-0038](0038-item-and-category-screens.md) | Item and category screens | T1.14 | Accepted |
| [ADR-0039](0039-units-packages-and-conversions.md) | Units, packages and conversions | T1.15 | Accepted |
| [ADR-0040](0040-the-ledger-and-its-posting-service.md) | The ledger and its posting service | T1.16 | Accepted |
| [ADR-0041](0041-opening-balances.md) | Opening balances | T1.17 | Accepted |
| [ADR-0042](0042-writers-through-the-ledger.md) | Every stock writer posts through the ledger | T1.18 | Accepted |
| [ADR-0043](0043-exceptions.md) | Exceptions: one record for everything that needs a person | T1.19 | Accepted |
| [ADR-0044](0044-the-job-queue.md) | The job queue and scheduled evaluation for every business | T1.20 | Accepted |
| [ADR-0045](0045-nightly-ledger-verification.md) | Nightly ledger verification | T1.21 | Accepted |
| [ADR-0046](0046-adjustments-and-movement-history.md) | Adjustments and movement history | T1.22 | Accepted |
| [ADR-0047](0047-count-sessions.md) | Count sessions | T1.23 | Accepted |
| [ADR-0048](0048-cost-basis-and-valuation.md) | Cost basis and valuation | T1.24 | Proposed |
