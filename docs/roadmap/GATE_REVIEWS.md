# Gate reviews

Evidence for each stage gate's pass condition ([TESTING_AND_QUALITY_STRATEGY §5](TESTING_AND_QUALITY_STRATEGY.md#5-stage-exit-gates)),
prepared for the owner. A gate passes only when the owner signs it off ([README §4](README.md#4-governance),
rule 9). Gate G0 is recorded in the execution log (2026-11-06).

Stage 1 ends when gates G1 and G2 both pass ([MASTER_DEVELOPMENT_ROADMAP](MASTER_DEVELOPMENT_ROADMAP.md),
Stage 1 exit criteria). Neither has been signed off yet.

---

## G2 Ledger engine (M2)

**Status:** prepared 2026-11-13, awaiting the owner's sign-off.

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

**Status:** every M1 task (T1.01 to T1.12) was merged by 2026-11-11. The sign-off was never
recorded; the execution log noted it as the owner's. The evidence is still green at `75f07f2`.

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
| G1 Tenancy & access | pending | |
| G2 Ledger engine | pending | |

With both passed, Stage 1 is complete and Stage 2 starts with T2.01 Owned product catalog.
