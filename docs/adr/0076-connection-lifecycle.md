# ADR-0076: The POS connection lifecycle

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.02: POS-FR-01, SQ-01..04, DR-22, AC-POS-01, AC-POS-09,
  AC-POS-11; DM-14)
- **Tasks:** T3.02; T3.03 (location mapping) and T3.05 (ingestion) read connections through it

## Context

A business could connect Square but never disconnect (SQ5). It asked for inventory write and read
scopes it does not use in R1 (S6). Nothing stopped two businesses holding the same merchant, and
webhooks then went to whichever row came first (S7, probe PA4). A revoked authorization surfaced
only as a failed sync.

## Decision

1. **One writer.** `app/connections.py` is the only code that creates, re-tokens or closes a
   `provider_connections` row (a test enforces it). It is provider-neutral: it reaches a POS
   through `adapter_for()` and the contract's `Credentials` (ADR-0075), and keeps tokens
   encrypted through `app/crypto.py`.
2. **Statuses** are `active`, `revoked` (the provider ended it) and `disconnected` (the business
   did), held by a CHECK. An active connection holds a token; a disconnected one holds none.
3. **One business per merchant.** A partial unique index allows one active connection per
   (provider, merchant) across all businesses. This is stricter than DR-22's merchant +
   location, and it is the reading Square forces: its revoke endpoint ends every token the
   merchant granted the application, so two businesses sharing a merchant would disconnect each
   other. A second business is refused (`MerchantTaken`) without being told which business holds
   the merchant, and the token it just obtained is dropped, never revoked. The DM-14 migration
   refuses to run while duplicates exist rather than choosing which business keeps the merchant.
4. **Scopes** are merchant profile, items and orders. Inventory read and write are requested
   only while `SQUARE_WRITE_BACK` is on (SQ-02, C-34); without it, the legacy sync no longer
   reads Square's counts, which changed no stock anyway.
5. **Refresh** happens seven days before expiry. A provider answer of 401 (to a refresh or an API
   call), an expired token with nothing to refresh it, or Square's signed
   `oauth.authorization.revoked` webhook marks the connection `revoked`. It also opens a critical
   `sync_failure` about the connection and queues `connection_revoked_email`, a job that emails
   the active Owners once (the exception's `notified_at`). Other failures leave the connection
   active.
6. **Disconnect** (`/app/connection`, `pos.manage`, verified email) revokes at the provider,
   clears the tokens, stops ingestion and keeps every record. If the provider cannot confirm,
   the business is disconnected here anyway and told to remove the app in Square. Reconnecting
   reuses the business's row and resolves the open `sync_failure`. Connect, reconnect,
   disconnect and revocation are audited.
7. **The connect routes go through the adapter.** `app/web/dashboard.py` no longer imports
   Square code; the provider seams are the legacy sync and the webhook receiver.

## Alternatives considered

- **Merchant + location uniqueness now:** a merchant split across businesses cannot survive one
  of them disconnecting (point 3). Square locations are mapped in T3.03, inside the one business
  that holds the merchant.
- **Treating every refresh failure as revocation:** an outage would end every connection.

## Consequences

- Existing connections keep their broader scopes until the merchant reconnects.
- A misconfigured application secret makes Square answer 401, which marks connections revoked;
  the tokens stay encrypted on the row, so an operator can restore them.
- The connection page does not yet show the merchant's business name: the adapter contract does
  not read the merchant profile.
