# ADR-0007: Frontend interactivity

- **Status:** Accepted, 2026-11-05 (owner: "Approve all 23", recorded as C-43 in [PRODUCT_DECISIONS](../product/PRODUCT_DECISIONS.md))
- **Decision ID:** ID-07 in [IMPLEMENTATION_DECISIONS](../roadmap/IMPLEMENTATION_DECISIONS.md#1-decisions-approved-2026-11-05)
- **Tasks:** T2.21

## Context

Floor tasks need partial updates on phones; baseline audit advised against a CSS/SPA swap

## Decision

Keep server-rendered Jinja; allow **HTMX** (vendored, no build step) for floor screens only

## Alternatives considered

Vanilla JS only; SPA framework

## Consequences

Not built yet (T2.21). Since T0.06 every page carries a Content-Security-Policy that runs only same-origin scripts and inline scripts carrying the request's nonce: vendored HTMX loads, but features that evaluate code from attributes (`hx-on`) are blocked and must not be used.
