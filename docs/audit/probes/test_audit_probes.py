"""Audit probes: each test asserts the CURRENT (suspected-defective) behaviour, so a pass confirms the finding."""
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.models import StockBatch, StockItem, TrackMode
from app.units import Dimension


# R1 (count-day sales never deducted) and R2 (recipe consumption ignores location) retired in
# T1.18: the derived engine is gone and sales move no stock until the consumption engine (T2.05);
# tests/test_ledger_writers.py::test_r1_r2_sales_never_change_stock_without_a_movement.


def _provider_item(db, tenant, location, qty="10"):
    item = StockItem(tenant_id=tenant.id, location_id=location.id, slug="cups", name="Cups",
                     dimension=Dimension.COUNT, stock_unit="each", track_mode=TrackMode.PROVIDER,
                     provider_variation_id="VAR1", qty_base=Decimal(qty))
    db.add(item)
    db.commit()
    return item


# R3 (failed Square push on receipt swallowed, then overwritten) retired in T0.08: nothing is
# written to Square in R1 (C-34); tests/test_square_writeback.py asserts zero write calls.


# R4 (concurrent apply raises stock twice) retired in T1.18: the document is claimed with a
# conditional update before one receipt is posted (AC-RCV-05);
# tests/test_ledger_writers.py::test_r4_two_requests_that_both_saw_it_unapplied_receive_it_once.


# R5 (POS-driven balance changes leave no movement; duplicates not deduplicated) retired in
# T1.18: Square counts no longer change stock at all (C-34);
# tests/test_ledger_writers.py::test_r5_a_square_count_never_changes_stock_and_a_repeat_is_harmless.


def test_R6_batch_removal_is_a_hard_delete(db, tenant, location):
    item = _provider_item(db, tenant, location)
    db.add(StockBatch(tenant_id=tenant.id, stock_item_id=item.id, received_qty_base=Decimal("5"),
                      expires_on=date(2026, 12, 1), source="label"))
    db.commit()
    from app.main import app
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        batch_id = db.query(StockBatch).one().id
        r = client.post(f"/app/expiring/{batch_id}/remove", follow_redirects=False)
        assert r.status_code == 303
    db.expire_all()
    assert db.query(StockBatch).count() == 0


# R7 (invoice image served with the uploader's Content-Type) retired in T0.06: the finding is fixed
# and tests/test_receiving_web.py::test_html_named_jpg_and_sent_as_html_is_refused asserts the
# safe behaviour in CI.


# R8 (forms carry no CSRF token; login is not throttled) retired: its CSRF half in T1.07
# (tests/test_sessions.py::test_the_login_page_carries_a_form_token) and its throttling half in
# T1.09 (tests/test_throttling.py::test_five_wrong_passwords_lock_the_account_even_against_the_right_one).
