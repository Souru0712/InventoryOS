# ADR-0026: Scoped data access: OrgContext, scoped helpers, named system lookups

- **Status:** Accepted, 2026-11-06 (the owner merged the T1.02 pull request, #20: "Yes, merge it")
- **Decision ID:** none (implements C-33 in T1.02; DR-09, DR-35, SAAS-T-04)
- **Tasks:** T1.02; shapes T1.03 (isolation suite), T1.05 (memberships), T1.06 (permissions)

## Context

Isolation was per-route convention (audit S8): a route loaded a row by id with `db.get` and then
compared its `tenant_id`, and every service filtered by a `tenant_id` integer it was handed. One
forgotten comparison exposes another business's data. C-33 decided a mandatory scoped data-access
layer, with unscoped access blocked by a test.

## Decision

1. **`OrgContext`** (`app/access/context.py`): frozen; `tenant_id`, `user_id` (None for the
   system), `location_ids` (None for every location). Built only by `OrgContext.for_membership`
   (the `require_org` dependency, from the session's active membership since T1.05, ADR-0028) or
   `OrgContext.system(tenant_id)` (scheduled jobs, signed webhooks, the command line), never from
   request input.
2. **Every service takes `org: OrgContext`** where it took `tenant_id: int`, so memberships and
   location scopes (T1.05) reach every query without changing a signature again.
3. **Queries go through `select_scoped`, `get_scoped`, `update_scoped`** (`app/access/scoped.py`).
   They add the tenant filter, and the location filter for a location-limited context. They refuse
   rather than guess: a table owned by no organization, a table without `tenant_id`, or (for a
   location-limited context) a table whose location is known only through a parent raises
   `UnscopedAccess`. Organization-wide tables (tenant, users, connections, sold items, sync runs,
   email log, health snapshots) are not narrowed by location.
4. **Cross-organization queries are named functions in `app/access/system.py`**: sign-in by
   email, the provider connection a webhook names, the scheduler's tenant lists, and the Stripe
   account lookups. Their result is then worked on through an `OrgContext` for that organization.
5. **A static test** (`tests/test_scoped_access.py`) fails on `db.get`/`session.get`, `.query()`,
   and any import of `select`, `update`, `delete`, `insert` or `text` from SQLAlchemy in `app/web`
   and `app/orchestration`; a second test proves it catches the old pattern.

`OrgContext` carries no permissions yet: the permission catalogue and its keys arrive in T1.06,
and a field nothing could check would be dead weight.

## Alternatives considered

- Keep `tenant_id` parameters and build the context inside each service: queries are scoped, but
  the user and location scope never reach the services, so T1.05 would change every signature.
- A session-level filter (SQLAlchemy `do_orm_execute` with `with_loader_criteria`): invisible at
  the call site and easy to switch off by accident; C-33 asks for an explicit layer.
- Postgres row-level security now: decided for R1.1 as a second guard (ADR-0022).

## Consequences

- New routes take `org: OrgContext = Depends(require_org)`; new services take `org`; a lookup that
  must cross organizations is added to `app/access/system.py` with a docstring naming its one job.
- The scan covers routes and services. `app/load` (provider writers, replaced through the ledger in
  T1.16–T1.18), `app/notify`, `app/security`, `app/entitlements` and the command line keep their own
  tenant filters until those tasks.
- A page narrowed by `?location=` takes it through `location_filter` (`app/security.py`), which
  answers 404 for a location outside the organization (added in T1.03; before, such a page
  answered 200 and showed nothing).
- Built in T1.02. The isolation suite (T1.03, `tests/test_isolation.py`) exercises it from the
  route table.
