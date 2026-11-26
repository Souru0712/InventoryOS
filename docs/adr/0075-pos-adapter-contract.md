# ADR-0075: The POS adapter contract

- **Status:** Accepted, 2026-11-26 (owner: "Merge it"; PR #63, merge commit bc67035)
- **Decision ID:** none (implements T3.01: POS-FR-15, DR-29, INTEGRATION_REQUIREMENTS §2,
  AC-POS-12, AC-SAL-07)
- **Tasks:** T3.01; T3.02–T3.12 build on it

## Context

Square must not become the architecture (brief §3.1): a second POS later has to be an adapter,
not a rewrite. The Square code existed (client, OAuth, webhooks, a normaliser producing dicts)
but no contract said what any POS adapter returns.

## Decision

1. **`app/providers/base.py`** holds the contract and nothing provider-specific: the normalized
   types of INTEGRATION_REQUIREMENTS §2 as frozen dataclasses (`Credentials`,
   `NormalizedLocation`, `NormalizedProduct`, `NormalizedModifier`, `NormalizedOrder` with lines
   by uid, modifiers and refunds, `NormalizedEvent`, `OrderPage`), the `PosAdapter` protocol
   (authorize, exchange, refresh, revoke, list locations, sync catalog, fetch orders, parse a
   webhook), `Unsupported` for a capability an adapter lacks, and a `Registry`.
2. **`app.providers.adapter_for(code)`** is the only way business code reaches a provider; the
   provider packages load lazily there.
3. **Square is the first implementation** (`app/providers/square/adapter.py`), wrapping the
   existing client, OAuth and signature check; `normalize.py` gains converters into the
   normalized types. Revocation answers `Unsupported` until the connection lifecycle (T3.02).
4. **The boundary check** (`tests/test_standalone.py`) now allows the contract and the registry
   anywhere, keeps provider packages to the POS seams, and checks the contract names no
   provider. Contract tests run on recorded Square payloads with no network, and a fake adapter
   fills the contract with no Square type (AC-POS-12).

## Alternatives considered

- **Abstract base classes:** a protocol lets an adapter be any object with the methods, and the
  registry still checks it at registration.
- **Rewiring the legacy sync now:** T3.05 builds ingestion on the contract and T3.12 removes the
  legacy path.

## Consequences

- The legacy sync (`app/orchestration/sync.py`) still calls the Square client directly until T3.12.
