# ADR-0062: Receivings on the ledger

- **Status:** Proposed, 2026-11-19 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T2.11-a: RCV-FR-01, -05..07, -09..12, DR-07, AC-RCV-01,
  -05..08; DM-12)
- **Tasks:** T2.11 (`-a` posting model, `-b` invoice review on receivings and the extraction job)

## Context

Receiving lived only as invoice documents applied in one step. RCV-FR asks for manual receiving,
invoiced/arrived/accepted per line with discrepancies, price checks against supplier items,
exactly-once posting, and corrections that keep the original.

## Decision

1. **`receivings`** (location, supplier, optional document, draft or posted) and
   **`receiving_lines`** (item, optional supplier item, ordered/invoiced/arrived/accepted, unit,
   accepted base quantity, price per unit, expiry, lot, reject reason, price flag, status,
   batch, transaction, the line it corrects), written only by `app/receivings.py`
   (migration `ebcbee00f30e`).
2. **Posting exactly once:** a conditional update moves the receiving from draft to posted; a
   second request finds it posted and gets `AlreadyPosted` (AC-RCV-05).
3. **Per line:** each accepted line posts its own `receipt` transaction keyed
   `receiving_line:<id>`, into a new batch when dated; its price becomes the item's cost and, in
   the supplier item's unit, the supplier item's last price, flagged when 10 % or more from the
   last (AC-RCV-07). A supplier item's unit converts by its pack (AC-PRC-02).
4. **Discrepancies** (short, over, rejected with reason) are kept on the line and summarised in
   one `receiving_discrepancy` per receiving (AC-RCV-06).
5. **Correction** (AC-RCV-08): the line's receipt is reversed, it is marked corrected, and a new
   line naming it is posted; both stay visible.
6. **DM-12:** applied documents get posted receivings with their applied lines, linked to their
   existing receipt transaction; no movement is written.
7. **Screens** `/app/deliveries` for receiving by hand; invoice photos keep `/app/receiving`.

## Consequences

- **T2.11-b:** invoice review creates and posts a receiving (so documents stop posting on their
  own), extraction as a job with a per-business daily cap, Employee cost changes flagged.
