# Gate reviews

Evidence for each stage gate's pass condition ([TESTING_AND_QUALITY_STRATEGY §5](TESTING_AND_QUALITY_STRATEGY.md#5-stage-exit-gates)),
prepared for the owner. A gate passes only when the owner signs it off ([README §4](README.md#4-governance),
rule 9). Gate G0 is recorded in the execution log (2026-11-06).

Stage 1 ends when gates G1 and G2 both pass ([MASTER_DEVELOPMENT_ROADMAP](MASTER_DEVELOPMENT_ROADMAP.md),
Stage 1 exit criteria). The owner signed off both on 2026-11-13 ("Sign off both"), so Stage 1
is complete.

Stage 2's gates G3 and G4 have no review yet: Stage 2 is merged, but its evidence has not been
gathered here.

---

## G5 Square connected (M5)

**Status:** evidence prepared 2026-11-25; not yet passed. It needs:
1. CI green on PostgreSQL. The Stage 3 pull request (`stage3` → `main`) is not open yet, so no
   CI run covers this branch.
2. The owner's sign-off.

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
  - **PD-53 (open)** asks whether a size's first recipe may start earlier.
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

**Recommendation:** pass G5 once the Stage 3 pull request is green in CI, PostgreSQL included.
PD-43 to PD-53 stay open either way; none blocks the gate.

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
| G5 Square connected | Evidence prepared; awaiting CI on PostgreSQL (Stage 3 PR) and sign-off | — |

Stage 1 is complete. Stage 2 starts with T2.01 Owned product catalog.
