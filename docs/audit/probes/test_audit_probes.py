"""Audit probes: each test asserts the CURRENT (suspected-defective) behaviour, so a pass confirms the finding.

Every probe in this file has been retired; each note below names the CI test that now asserts the
fixed behaviour.
"""


# R1 (count-day sales never deducted) and R2 (recipe consumption ignores location) retired in
# T1.18: the derived engine is gone and sales move no stock until the consumption engine (T2.05);
# tests/test_ledger_writers.py::test_r1_r2_sales_never_change_stock_without_a_movement.


# R3 (failed Square push on receipt swallowed, then overwritten) retired in T0.08: nothing is
# written to Square in R1 (C-34); tests/test_square_writeback.py asserts zero write calls.


# R4 (concurrent apply raises stock twice) retired in T1.18: the document is claimed with a
# conditional update before one receipt is posted (AC-RCV-05);
# tests/test_ledger_writers.py::test_r4_two_requests_that_both_saw_it_unapplied_receive_it_once.


# R5 (POS-driven balance changes leave no movement; duplicates not deduplicated) retired in
# T1.18: Square counts no longer change stock at all (C-34);
# tests/test_ledger_writers.py::test_r5_a_square_count_never_changes_stock_and_a_repeat_is_harmless.


# R6 (batch removal is a hard delete) retired in T2.06: a removed date moves what the batch
# held back to undated stock and the batch stays with its history (DR-24);
# tests/test_batches.py::test_removing_a_date_through_the_page_keeps_the_batch.


# R7 (invoice image served with the uploader's Content-Type) retired in T0.06: the finding is fixed
# and tests/test_receiving_web.py::test_html_named_jpg_and_sent_as_html_is_refused asserts the
# safe behaviour in CI.


# R8 (forms carry no CSRF token; login is not throttled) retired: its CSRF half in T1.07
# (tests/test_sessions.py::test_the_login_page_carries_a_form_token) and its throttling half in
# T1.09 (tests/test_throttling.py::test_five_wrong_passwords_lock_the_account_even_against_the_right_one).
