# ADR-0081: Refunds, voids and discounts from the POS

- **Status:** Proposed, 2026-11-20 (built on the stacked branch; accepted when the owner merges it)
- **Decision ID:** none (implements T3.07: POS-FR-08 (record), POS-FR-09, POS-FR-26, DR-26, PD-10,
  WF-17, AC-POS-04, AC-POS-05)
- **Tasks:** T3.07; R1.1 adds restock or waste classification of a refunded item (PD-10)

## Context

Refunds were ignored, which overstated sales. A completed order voided at the POS kept its
consumption. `sales_returns` existed (T2.04), but nothing wrote it.

## Decision

1. **A completed refund is a sales return** (`record_pos_refund()` in `app/sales.py`, the owner
   of sales records). It is recorded once per refund id at the order's location, on the business
   date the money went back. It is linked to the order's sale, and each returned line to the
   sale line with the same key. A returned line's amount is its share of that line's net. It
   never puts stock back (DR-26). Pending, rejected and failed refunds wait until they complete.
2. **A refund that arrives before its order** keeps its original reference and its lines by key,
   and is linked when the order's sale is recorded (`link_waiting_refunds()`). A refund on a day
   the daily totals own stays unlinked, but still counts against net sales.
3. **A completed order voided at the POS** voids its sale (`void_sale()`), so what it took is
   given back (A-07: a void means it was not made). An order cancelled before completion was
   never a sale (T3.05).
4. **Discounts and comps change money, never consumption.** Consumption reads quantities; a line's
   net is gross less its discount.
5. **Net sales** are read through `sales_money()`: the source of record's counted sales, less its
   refunds on the days they were given. The units-based readers are unchanged.
6. **Square's shape**: a return names its original order (`source_order_id`), and that is the
   order a refund is about. With one refund on an order its returned lines are the refund's; with
   several, each is recorded by its amount alone. The sandbox run (T3.11) confirms this shape.
7. **The connection page** counts refunds and those not yet matched to an order. The amount is
   shown only to a role with `inventory.view_costs`.

## Finding for the owner

WF-17 asks for an info exception when a refund's order cannot be found, but REC-FR-12 lists no
exception type for it. Adding one changes the exception catalog, so it is proposed, not built.
**Proposal:** add `orphan_refund` (info) to REC-FR-12. Until then, the connection page counts the
refunds not yet matched to an order.

## Alternatives considered

- **Subtracting refunds inside `daily_sales()`:** its readers count units and days with sales, and
  a refund-only row would make a day look recorded.
- **Restocking refunded items:** DR-26 forbids it. Classification arrives in R1.1.

## Consequences

- Reports that show sales money must read `sales_money()` to be net of refunds.
