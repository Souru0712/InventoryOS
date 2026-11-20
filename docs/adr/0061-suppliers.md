# ADR-0061: Suppliers and supplier items

- **Status:** Accepted, 2026-11-19 (owner: "Update, then merge"; PR #60, merge commit f9184b7)
- **Decision ID:** none (implements T2.10: PRC-FR-01..03, -08, AC-PRC-01, -02; DM-11)
- **Tasks:** T2.10; T2.11 (receiving picks a supplier and records prices), T2.15 (reorder by
  supplier)

## Context

The supplier was free text on items and documents (DB7). Receiving and reorder need a real
supplier with its own purchase units, packs and prices, and a price that jumps should be seen.

## Decision

1. **`suppliers`** (contacts, ordering method, delivery days as a weekday mask, lead time,
   minimum order, notes, active) and **`supplier_items`** (the supplier's code and name, the
   purchase unit and how much of the item it holds in base units, last price per purchase
   unit, preferred, active), written only by `app/suppliers.py`. Suppliers are deactivated,
   never deleted (AC-PRC-01); one supplier item per item is preferred.
2. **Packs convert** through the item's own units (`entered_base`), so two suppliers' packs
   reach the same base units (AC-PRC-02, `in_base()`).
3. **Price changes:** `record_price()` keeps the last price and opens `price_variance` on the
   supplier item when the new price is 10 % or more from the last (PRC-FR-08).
4. **Purchase history** lists the deliveries received under the supplier's name (PRC-FR-03),
   until T2.11 links documents to suppliers.
5. **DM-11** (migration `30d75bcd3fd3`): distinct free-text names (trimmed, without case) become
   suppliers; each stock item naming one gets a preferred supplier item with its receive unit,
   pack and a last price from its cost. The free-text columns stay.
6. **Screens** `/app/suppliers`: list, add, detail with items and deliveries; viewing takes
   `reorder.view`, changing takes `supplier.manage`; prices only with cost access.

## Alternatives considered

- **Keep suppliers as aliases (`stock_aliases.supplier`):** no lead time, packs or prices.

## Consequences

- T2.11 sets a document's supplier and calls `record_price()` for each received line.
- Supplier changes are not audited (not in the audit categories); revisit with T2.11.
