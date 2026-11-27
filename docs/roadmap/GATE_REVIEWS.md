# Gate reviews

Evidence for each stage gate's pass condition ([TESTING_AND_QUALITY_STRATEGY §5](TESTING_AND_QUALITY_STRATEGY.md#5-stage-exit-gates)),
prepared for the owner. A gate passes only when the owner signs it off ([README §4](README.md#4-governance),
rule 9). Gate G0 is recorded in the execution log (2026-11-06).

Stage 1 ends when gates G1 and G2 both pass ([MASTER_DEVELOPMENT_ROADMAP](MASTER_DEVELOPMENT_ROADMAP.md),
Stage 1 exit criteria). The owner signed off both on 2026-11-13 ("Sign off both"), so Stage 1
is complete.

Stage 2's gates G3 and G4 were reviewed on 2026-11-25, after Stage 2 was merged. Neither has
passed: G3 waits on two manual checks, and G4 on an unbuilt criterion and two manual checks.

---

## G5 Square connected (M5)

**Status:** passed, 2026-11-26. Signed off by the owner ("sign off G5 as well"). PR #63's head
`dbeb10d` is green on all six jobs. PR #63 was merged on 2026-11-26 (`bc67035`), so Stage 3
is complete.

**CI evidence.** The Stage 3 pull request (#63, `stage3` → `main`) is green on all six jobs at
`601c7d1`: Tests (SQLite), Tests (PostgreSQL 16), Standalone (no POS), Lint and types,
Dependency audit, Docker image.

**Pass condition**
([TESTING_AND_QUALITY_STRATEGY §5](TESTING_AND_QUALITY_STRATEGY.md#5-stage-exit-gates)):
- AC-POS-01..12 pass, with the sandbox run recorded (T3.11);
- AC-SAL-06 passes;
- the Standalone job is still green;
- a duplicate delivery ×3 posts once;
- probe PA4 is retired.

**Local evidence** on `stage3` at `724c8e0`:
- the full suite on SQLite: 1327 passed, 33 skipped (the Postgres-only tests);
- the 30 tests cited below, run together on 2026-11-25: 30 passed.

The sandbox run (22–25 Nov, seller "InventoryOS", every step passing) is recorded in
`docs/EXECUTION-LOG.md` (T3.11).

### Pass condition, item by item

**1. AC-POS-01..12.** Met, with caveats (a) to (c).

| Criterion | Test | Sandbox run |
|---|---|---|
| AC-POS-01 connect, choose a location, import summary, tokens encrypted | `tests/test_connections.py::test_ac_pos_01_connecting_asks_for_product_scopes_and_stores_tokens_encrypted`; `tests/test_pos_catalog.py::test_the_connection_screen_shows_the_import_and_maps_a_location` | Steps 3, 4, 18: three read scopes only; "4 items, 5 variations and 3 modifiers"; consent screen shown on reconnect |
| AC-POS-02 a completed order posts consumption within 5 minutes (M) | `tests/test_order_ingestion.py::test_a_completed_order_is_one_sale_a_line_per_order_line_used_once` | Step 8: 2 × Latte 12 oz arrived by webhook in 3 s; milk −480 ml at the order's completion time |
| AC-POS-03 webhook ×3 and reconciliation consume once | `tests/test_webhook_jobs.py::test_ac_pos_03_three_deliveries_and_a_reconciliation_consume_once` | Step 9: the event resent 3 times, `duplicate` each time; revision 1; the scheduled re-read changed nothing |
| AC-POS-04 cancelled before completion uses nothing | `tests/test_refunds_voids.py::test_ac_pos_04_an_order_cancelled_before_completion_uses_nothing` | Step 11 |
| AC-POS-05 refund recorded, linked, lowers net, no stock | `tests/test_refunds_voids.py::test_ac_pos_05_a_refund_is_recorded_linked_lowers_net_sales_and_restocks_nothing`; `::test_a_return_orders_refund_counts_once_square_approves_it` | Step 12: failed first (defect, fixed), passed on retest |
| AC-POS-06 unmapped, then mapped and reprocessed, posts once at the original moment | `tests/test_mappings.py::test_ac_pos_06_mapping_then_reprocessing_posts_once_at_the_original_moment`; `::test_the_preview_reads_the_recipe_in_effect_at_each_sale_as_the_job_does` | Step 14, after a fix; see (b) |
| AC-POS-07 a missed webhook is recovered within 60 minutes | `tests/test_reconciliation.py::test_ac_pos_07_an_order_delivered_late_is_picked_up_within_the_hour` | Step 15: subscription off, the sale arrived by the 5-minute read in 2 minutes |
| AC-POS-08 a skipped order opens `pos_reconciliation_mismatch` | `tests/test_reconciliation.py::test_ac_pos_08_a_skipped_order_opens_a_mismatch_is_read_again_and_clears` | Step 16: "6 day(s) checked, 0 differed"; see (c) |
| AC-POS-09 revocation: revoked, critical `sync_failure`, Owner emailed | `tests/test_connections.py::test_ac_pos_09_a_revoked_authorization_is_marked_raised_and_emailed_to_the_owner` | Step 17: event in 12 s; see (a) |
| AC-POS-10 an invalid signature is refused and does nothing | `tests/test_webhook_jobs.py::test_ac_pos_10_an_invalid_signature_is_refused_and_does_nothing` | Step 10: 400, nothing stored |
| AC-POS-11 one merchant, one organization | `tests/test_connections.py::test_ac_pos_11_a_second_business_cannot_connect_the_same_merchant` | Step 5: "already connected to another business" |
| AC-POS-12 business logic needs no Square type | `tests/test_pos_adapter.py::test_ac_pos_12_another_adapter_needs_no_square_type` | Not a sandbox step |

Caveats:
- **(a) AC-POS-09:** the sandbox dashboard has no apps page, so the authorization was revoked
  through Square's API from outside the app. InventoryOS heard of it only by Square's signed
  `oauth.authorization.revoked` event, as it would when a seller removes the app.
- **(b) AC-POS-06** holds when the mapped size had a recipe in effect at the sale's moment.
  - A product first made from Square after it was sold cannot be given a recipe that covers that
    sale (DR-16). Its exception can only be dismissed.
  - PD-53 asked whether a size's first recipe may start earlier: yes, with an explicit date;
    past sales change only by an authorized reprocessing (C-51, 2026-11-26; to build).
- **(c) AC-POS-08:** a sandbox order cannot be skipped on purpose. The run proves a clean daily
  check; the suite proves that a mismatch is detected, read again and cleared.

**2. AC-SAL-06 passes.** Met.
- `tests/test_sales_modes.py::test_ac_sal_06_standalone_to_connected_from_a_past_cutover`;
- `tests/test_daily_sales.py::test_ac_sal_06_a_day_from_a_pos_is_not_entered_by_hand`;
- sandbox step 7: hand entry for a Square day refused.

**3. The Standalone job is still green.** Met locally; CI pending.
- `tests/test_standalone.py` passes on `stage3`, including the boundary checks that keep
  provider code at the POS seams.
- CI runs it as its own job, "Standalone (no POS)", on every pull request.

**4. A duplicate delivery ×3 posts once.** Met.
- AC-POS-03 above, in the suite and in sandbox step 9.

**5. Probe PA4 retired.** Met.
- Retired in T3.02: a partial unique index allows one active connection per merchant.
- `tests/test_connections.py::test_pa4_retired_a_merchant_is_actively_connected_to_one_business_at_most`;
  the note is in `docs/audit/probes/test_phase2_probes.py`.

**Also done in Stage 3**
- The legacy Square sync is removed (T3.12, ADR-0085). `tests/test_legacy_sync_removed.py`
  checks that no code path writes the legacy rows.
- Risk register:
  - TR-11 (nothing external run live): addressed by the sandbox run.
  - TR-25 (60-day re-read) and TR-29 (milligram unit): addressed by T3.05, T3.12 and the
    MEASUREMENT_UNIT fix.
  - TR-26 (never deployed): stays open until T0.09 or T4.09.

**Recommendation:** pass G5; the Stage 3 pull request is green in CI, PostgreSQL included.
None of PD-43 to PD-54 blocked the gate. PD-43 to PD-53 were decided on 2026-11-26 (C-50 to C-53); PD-54 stays open.

---

## G4 Standalone end-to-end (M4)

**Status:** evidence prepared 2026-11-25; AC-SAL-08 completed and AC-ANL-07 recorded
2026-11-26; not passed. It needs:
- one manual check: **AC-SAAS-10**;
- the owner's sign-off.

**Pass condition**
([TESTING_AND_QUALITY_STRATEGY §5](TESTING_AND_QUALITY_STRATEGY.md#5-stage-exit-gates)):
- the R1 criteria AC-SAL-01..05, -07, -08, AC-REC, AC-ROR and AC-ANL pass;
- the Standalone end-to-end job is green with no POS settings;
- AC-SAAS-10 is observed with a non-founder.

**CI evidence.** Stage 2 is merged. `main` at `2c496e3` (PR #62) is green on all six jobs
(2026-11-20):
- Tests (SQLite);
- Tests (PostgreSQL 16);
- Standalone (no POS);
- Lint and types;
- Dependency audit;
- Docker image.

The test files below and under G3 were run together on `stage3` on 2026-11-25: 266 passed, 2
skipped (the Postgres-only tests).

### Pass condition, item by item

**1. The R1 criteria.** Met.

| Criteria | Tests |
|---|---|
| AC-SAL-01 every R1 workflow with no POS | `tests/test_standalone.py::test_ac_sal_01_every_r1_workflow_with_no_pos` |
| AC-SAL-02, -03, -05 a day's sales: posts once, corrected by reversal with a reason, a missing day flagged | `tests/test_daily_sales.py` (`test_ac_sal_02_…`, `test_ac_sal_03_…`, `test_ac_sal_05_…`) |
| AC-SAL-04 file import: preview, unmatched listed, re-import adds nothing | `tests/test_sales_import.py::test_ac_sal_04_preview_posts_matched_lists_unmatched_and_reimport_adds_nothing` |
| AC-SAL-07 provider code only in the adapter | `tests/test_standalone.py::test_ac_sal_07_provider_code_lives_only_in_the_adapter` |
| AC-SAL-08 origin shown; Standalone coverage "N of M open days" | **Met (2026-11-26).** Every sale stores its origin (`tests/test_sales.py`, `tests/test_daily_sales.py`, `tests/test_sales_import.py`). The reports page names where each location's sales came from and, on Standalone days, "Sales on N of M open days"; so do cost of sales, its CSV, a `sales_coverage` CSV and the weekly summary (page and email): `tests/test_sales_coverage.py`. How M is counted is PD-54 (OPEN) |
| AC-REC-01..05 counts: storage order, expected at count time, one variance per line, review thresholds | `tests/test_count_sessions.py` (`test_ac_rec_01_…` to `test_ac_rec_04_…`), `tests/test_count_screens.py` (AC-REC-01, -05) |
| AC-REC-06..09 exceptions: one per problem, auto-resolve, dismiss with a reason, no amounts for Employees | `tests/test_exceptions.py` (`test_ac_rec_06_…` to `test_ac_rec_09_…`) |
| AC-ROR-01, -03, -05 reorder: worked example, count first, never orders | `tests/test_reorder.py` (`test_ac_ror_01_the_worked_example`, `test_ac_ror_03_count_first`, `test_ac_ror_05_…`) |
| AC-ROR-02, -04 inputs and formula shown; CSV grouped by supplier | `tests/test_reorder.py::test_the_page_explains_and_the_csv_is_grouped_by_supplier` |
| AC-ANL-01, -02 past figures at their own cost; valuation basis | `tests/test_costs.py` (`test_ac_anl_01_…`, `test_ac_anl_02_…`) |
| AC-ANL-03..06, -08 reports on the ledger, business days, exports, usage between counts | `tests/test_reports.py`, `tests/test_reports_ii.py`, `tests/test_reorder.py::test_ac_anl_08_use_from_two_counts` |
| AC-ANL-07 the weekly summary email reaches Owners (M) | **Met (2026-11-26).** The demo business's weekly summary (week of 16 Nov) was sent through Resend from `reports@vontry.app` (domain verified) to an Owner at the owner's Gmail, and the owner reported it delivered. It carried stock value $656.35, waste $0.00, money at risk $154.01, low stock 1 and 11 open exceptions, the same figures as `/app/report`. Sent from a local run (no staging exists yet, TR-26): the repeat on staging stays with PD-52 and T4.09. A copy to `support@vontry.app` bounced (no routing rule for that address yet), which is a mailbox setup, not the summary |

**2. The Standalone end-to-end job is green with no POS settings.** Met.
- The CI job "Standalone (no POS)" passed on `main` (`2c496e3`).
- It also passes on `stage3`, with the Stage 3 boundary checks.

**3. AC-SAAS-10 observed with a non-founder (M).** **Not done.** Someone other than the founder
has to complete onboarding and record receiving, waste, production and counts. This is the
owner's to arrange (CUSTOMER_VALIDATION_PLAN: Standalone pilots).

**To pass G4:**
1. ~~Build AC-SAL-08's two missing parts.~~ Done 2026-11-26 (origin on sales-derived reports,
   "N of M open days" on Standalone reports).
2. ~~Record AC-ANL-07 once email delivery is configured.~~ Done 2026-11-26 (delivered through
   Resend).
3. Observe AC-SAAS-10 with a non-founder.

---

## G3 Operations engine (M3)

**Status:** evidence prepared 2026-11-25; AC-WST-01 recorded 2026-11-26; not passed. It needs
the manual half of **AC-RCV-04** and the owner's sign-off.

**Pass condition**
([TESTING_AND_QUALITY_STRATEGY §5](TESTING_AND_QUALITY_STRATEGY.md#5-stage-exit-gates)):
- the R1 criteria AC-RCP, AC-PRD, AC-WST, AC-BAT and AC-RCV pass;
- waste is counted once (AC-WST-08);
- the batch conservation property test passes (DR-13);
- probe R6 is retired (T2.06).

**CI evidence:** `main` at `2c496e3` is green on all six jobs, as under G4.
`test_ac_rcv_02_…`, added during this review, is on `stage3` (PR #63).

### Pass condition, item by item

**1. The R1 criteria.** Met, except AC-RCV-04's manual half.

| Criteria | Tests |
|---|---|
| AC-RCP-01..09 recipes: mixed units, versions, card, modifiers, prepared items, scaling, cost changes, sold without a recipe, Employee view | `tests/test_recipes.py`, `tests/test_recipe_screens.py`, `tests/test_costing.py`, `tests/test_consumption.py` (one `test_ac_rcp_0n_…` each) |
| AC-PRD-01..07 production: inputs to a dated batch, yield and cost, unknown cost, made-ahead vs made-to-order, void, FEFO inputs, finished goods | `tests/test_production.py`, `tests/test_consumption.py` (`test_ac_prd_0n_…`) |
| AC-WST-01 an Employee logs waste in ≤ 3 taps at 375 px (M) | **Met (2026-11-26).** Checked in the browser at 375 × 812 on a local demo café (`stage3` at `0290f43`), signed in as an Employee who picked themself on `/app/floor`. After choosing Whole milk, the form fits one screen with the unit and the batch (soonest-expiring) already set: 1 the quantity (2), 2 the reason (Spoilage), 3 "Log waste". "Waste logged. You can void it for 30 minutes." The ledger holds `waste:1`, −2,000 ml from the batch dated 27 Nov, recorded with the Employee as actor |
| AC-WST-02..08 waste: reason required, counted once everywhere, a product's recipe, 30-minute void, report and found-short | `tests/test_waste.py` (`test_ac_wst_02_…`, `test_ac_wst_03_and_08_…`, `test_ac_wst_04_…`, `test_ac_wst_05_…`, `test_ac_wst_06_and_07_…`) |
| AC-BAT-01..08 batches: dated receipt, FEFO/FIFO, inferred vs explicit, conservation, expired kept until counted, expiry flags, auto-dispose, a batch's movements | `tests/test_batches.py`, `tests/test_expiry_actions.py` |
| AC-RCV-01, -05..08 manual delivery, posts once (also concurrently), short and rejected, packs and price jumps, correction by reversal | `tests/test_receivings.py` (`test_ac_rcv_0n_…`), `tests/test_ledger_writers.py::test_ac_rcv_05_two_simultaneous_applies_post_one_set_of_movements` |
| AC-RCV-02 an invoice photo changes nothing until posted | `tests/test_receiving_flow.py::test_ac_rcv_02_an_uploaded_and_read_invoice_changes_nothing_until_posted` (added in this review) |
| AC-RCV-03 the same file twice is one invoice | `tests/test_receiving_flow.py::test_upload_is_deduplicated_by_content` |
| AC-RCV-04 extraction proposes lines to review; without it, manual entry (A+M) | Automated half met: lines wait for review (`test_parse_matches_what_it_recognises_and_flags_the_rest`, `test_unchecked_lines_are_skipped_not_applied`); an unreadable photo is left for a person (`test_an_unreadable_photo_leaves_the_document_for_a_human`); manual entry posts (`test_ac_rcv_01_…`). **Manual half not recorded:** a real invoice read and reviewed by the owner |
| AC-RCV-09 a learned match is suggested on the next invoice | `tests/test_receiving_flow.py::test_supplier_wording_is_learned_so_the_next_invoice_is_automatic`, `tests/test_receiving.py::test_matching_prefers_a_learned_alias`. The criterion says "supplier code", but C-47 rules codes out, so the learned key is the supplier's wording |
| AC-RCV-10 extraction beyond the daily cap is refused; manual entry still works | `tests/test_receivings.py::test_reading_invoices_stops_at_the_daily_cap` (the refused invoice waits for entry "by hand") |

**2. Waste counted once (AC-WST-08).** Met.
- `tests/test_waste.py::test_ac_wst_03_and_08_two_pounds_of_carrots_count_once_everywhere`.

**3. The batch conservation property test (DR-13).** Met.
- `tests/test_batches.py::test_conservation_holds_after_any_sequence`.

**4. Probe R6 retired.** Met.
- Retired in T2.06: a removed date moves what the batch held instead of deleting it.
- The note is in `docs/audit/probes/test_audit_probes.py`.

**To pass G3:**
1. ~~Record AC-WST-01.~~ Done 2026-11-26: three taps at 375 px.
2. Record AC-RCV-04's manual half: one real invoice read and reviewed.

---

## G2 Ledger engine (M2)

**Status:** passed, 2026-11-13. Signed off by the owner ("Sign off both"; PR #49).

**CI evidence.** Every M2 task (T1.13 to T1.25) is merged. `main` at `75f07f2` (PR #48) is green
on all five required jobs. On its pull-request head `8b99bf4`:
- SQLite: 977 passed, 25 skipped (the Postgres-only tests);
- PostgreSQL 16: 1002 passed, 0 skipped. That job allows no skips, so every Postgres-only test
  below ran there.

### Pass condition, item by item

**1. AC-INV-01..11 pass on Postgres, including a real concurrent-posting test.** Met.

| Criterion | Test | Notes |
|---|---|---|
| AC-INV-01 create, edit, deactivate | `tests/test_catalog.py`: `test_an_item_is_created_once_and_stocked_at_the_chosen_locations`, `test_editing_an_item_changes_it_everywhere_it_is_shown`, `test_a_deactivated_item_keeps_its_history_and_leaves_pick_lists` | |
| AC-INV-02 exact conversion | `tests/test_units_packages.py::test_ac_inv_02_same_dimension_and_packages_convert_exactly` | 907.1847 g; 22,712.47 ml |
| AC-INV-03 conversion required | `tests/test_units_packages.py::test_ac_inv_03_another_dimension_needs_the_items_own_conversion` | |
| AC-INV-04 balance = Σ movements | `tests/test_ledger.py::test_ac_inv_04_after_any_sequence_the_balance_is_the_sum_of_movements` | 1,000 seeded random sequences over two locations, with reversals |
| AC-INV-05 no update or delete | `tests/test_ledger.py::test_ac_inv_05_only_the_posting_service_writes_the_ledger`, `test_the_scoped_layer_refuses_to_update_posted_movements` | No route edits a movement at all, and the scoped layer refuses an update. See caveat (b) |
| AC-INV-06 idempotency | `tests/test_ledger.py::test_ac_inv_06_the_same_key_twice_posts_once_and_answers_the_same`; on Postgres `tests/test_ledger_concurrency.py::test_the_same_key_posted_twice_at_once_posts_once` | |
| AC-INV-07 concurrent postings | `tests/test_ledger_concurrency.py::test_ac_inv_07_a_receipt_and_waste_at_the_same_moment_both_count` | Postgres, 50 trials. Also covered: opposite-order postings without deadlock, two first postings to a new item, a double reversal |
| AC-INV-08 adjustment with reason | `tests/test_adjustments.py::test_ac_inv_08_an_adjustment_posts_with_its_reason_and_shows_in_history` | |
| AC-INV-09 below zero needs override | `tests/test_adjustments.py::test_ac_inv_09_below_zero_needs_an_override_with_a_reason` | See caveat (a) |
| AC-INV-10 sale larger than on hand | `tests/test_adjustments.py::test_ac_inv_10_a_sale_larger_than_on_hand_posts_and_asks_for_a_count` | |
| AC-INV-11 injected drift | `tests/test_ledger_verification.py::test_ac_inv_11_an_injected_mismatch_is_found_the_same_night_and_nothing_changes` | |

**2. Static test: only the posting service writes stock.** Met.
- `tests/test_ledger.py::test_ac_inv_05_only_the_posting_service_writes_the_ledger`: nothing
  outside `app/ledger/` names a ledger model.
- `tests/test_ledger_writers.py::test_nothing_outside_the_posting_service_assigns_a_quantity`:
  covers `stock_items.qty_base`, the projection screens read (ADR-0042).
- `tests/test_ledger_writers.py::test_the_legacy_tables_are_frozen`: covers the old count and
  event tables (DM-7).

**3. Σ movements = balance for every fixture.** Met.
- The property test above checks every item at every location after each of 1,000 sequences.
- `tests/test_opening_balances.py`: every demo item opens at what the product shows, and the
  ledger carries on from there.
- `tests/test_ledger_verification.py::test_g2_the_demo_business_verifies_clean_from_its_seed`
  (added for this review): the demo business, seeded as a developer seeds it, has balance =
  Σ movements = shown stock for every item and location.
- In production the same check runs for every business every night
  (`test_each_business_is_checked_once_a_night_and_only_its_own_ledger`).

**4. Nightly verification detects an injected drift.** Met.
- AC-INV-11 above.
- `test_a_screen_out_of_step_with_its_balance_is_drift_too`.
- `test_the_scheduler_queues_the_verification_every_night` (02:45).
- It raises `balance_drift` and never corrects either figure (ADR-0045).

**5. Probes R1, R2, R4, R5 and R6 retired.** Met for R1, R2, R4 and R5. R6 was moved by the
owner.

| Probe | Status | Replaced by |
|---|---|---|
| R1 count-day sales never deducted, R2 recipe consumption ignores location | Retired in T1.18 | `tests/test_ledger_writers.py::test_r1_r2_sales_never_change_stock_without_a_movement` |
| R4 concurrent apply raises stock twice | Retired in T1.18 | `tests/test_ledger_writers.py::test_r4_two_requests_that_both_saw_it_unapplied_receive_it_once`, `test_ac_rcv_05_two_simultaneous_applies_post_one_set_of_movements` |
| R5 POS balance changes leave no movement | Retired in T1.18 | `tests/test_ledger_writers.py::test_r5_a_square_count_never_changes_stock_and_a_repeat_is_harmless` |
| R6 batch removal is a hard delete | **Moved to T2.06 by the owner, 2026-11-12** | Still asserted by `docs/audit/probes/test_audit_probes.py` (outside CI). T2.06's tests replace it |

The gate tables still listed R6 under G2. This review moves it to G3, the gate of T2.06's
milestone M3, in the testing strategy and the master roadmap, to match the owner's decision.

### Caveats (non-blocking)
- **(a) AC-INV-09 names waste.** No waste screen exists until T2.07, so the rule is proven on
  adjustments, which post through the same below-zero guard (`refuse_below_zero=True`, DR-08).
  T2.07 must show the same for waste.
- **(b) AC-INV-05 says "via the API returns an error".** There is no API path to a posted
  movement to call, so nothing can reach one. The scoped layer refuses any update, and the
  static test keeps it that way.

**Recommendation:** pass G2.

---

## G1 Tenancy & access (M1)

**Status:** passed, 2026-11-13. Signed off by the owner ("Sign off both"; PR #49). Every M1
task (T1.01 to T1.12) was merged by 2026-11-11, and the evidence was still green at `75f07f2`.

| Pass condition | Evidence |
|---|---|
| Isolation suite passes for 100 % of id routes and file routes | `tests/test_isolation.py`: `test_every_route_that_takes_an_identifier_has_a_case` fails for any route without a case, including the invoice image route; `test_another_business_identifiers_reach_nothing` runs every case |
| Permission matrix passes for every key × role | `tests/test_permissions.py`: `test_the_owner_holds_every_key_and_the_presets_follow_the_matrix`, `test_every_route_declares_a_permission_or_is_public_or_personal` |
| Static test finds no unscoped query | `tests/test_scoped_access.py`: `test_routes_and_services_never_query_outside_the_access_layer`, `test_the_scan_catches_what_routes_used_to_do` |
| Probes PA1, PA3, PA5 and R8 retired | PA1 in T1.04 (`tests/test_locations.py::test_a_business_without_square_adds_a_location_then_its_stock`); PA3 in T1.05 (`tests/test_memberships.py::test_one_person_can_belong_to_two_businesses_but_only_once_to_each`); PA5 in T1.01 (`tests/test_schema_hygiene.py::test_a_stored_instant_comes_back_aware_and_in_utc`); R8 in T1.07 and T1.09 (`tests/test_sessions.py::test_the_login_page_carries_a_form_token`, `tests/test_throttling.py::test_five_wrong_passwords_lock_the_account_even_against_the_right_one`) |

**Recommendation:** pass G1.

---

## Sign-off

| Gate | Owner's decision | Date |
|---|---|---|
| G1 Tenancy & access | Passed ("Sign off both") | 2026-11-13 |
| G2 Ledger engine | Passed ("Sign off both"); R6 moved to G3 as decided on 2026-11-12 | 2026-11-13 |
| G3 Operations engine | Evidence prepared; AC-WST-01 recorded; awaiting AC-RCV-04's manual half, then sign-off | — |
| G4 Standalone end-to-end | Evidence prepared; AC-SAL-08 built; AC-ANL-07 recorded; awaiting AC-SAAS-10, then sign-off | — |
| G5 Square connected | Passed ("sign off G5 as well"); PR #63 green on all six jobs at `dbeb10d` | 2026-11-26 |

Stage 1 is complete. Stage 2 starts with T2.01 Owned product catalog.
