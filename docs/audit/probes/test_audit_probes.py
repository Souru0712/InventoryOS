"""Audit probes: each test asserts the CURRENT (suspected-defective) behaviour, so a pass confirms the finding."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.extract.invoice import StubExtractor
from app.load.upsert import apply_provider_counts
from app.models import (
    Document, DocumentStatus, InventoryCount, InventoryEvent, Location, RecipeComponent,
    SalesDaily, SoldItem, StockBatch, StockItem, TrackMode, WebhookEvent,
)
from app.orchestration import receiving as receiving_mod
from app.orchestration.receiving import LineDecision, apply_document, parse_document, save_upload
from app.orchestration.views import refresh_derived_quantities
from app.transform.inventory import derived_quantity
from app.units import Dimension, to_base


def test_R1_count_day_sales_after_the_count_are_never_deducted():
    """Count 1000 ml at 08:00; 300 ml sold later that same day. Derived stays 1000 forever."""
    anchor_at = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
    daily = {date(2026, 11, 4): Decimal("300"), date(2026, 11, 5): Decimal("200")}
    assert derived_quantity(Decimal("1000"), anchor_at, daily, date(2026, 11, 4)) == Decimal("1000")
    # Next day: only day-2 sales are deducted; the 300 ml from the count day are lost.
    assert derived_quantity(Decimal("1000"), anchor_at, daily, date(2026, 11, 5)) == Decimal("800")


def _two_locations(db, tenant, location):
    other = Location(tenant_id=tenant.id, provider="demo", provider_location_id="loc-2", name="Second")
    db.add(other)
    db.flush()
    return other


def test_R2_recipe_consumption_ignores_location(db, tenant, location):
    """Milk lives at location A. Lattes sold only at location B still drain A's milk."""
    other = _two_locations(db, tenant, location)
    milk = StockItem(tenant_id=tenant.id, location_id=location.id, slug="milk", name="Milk",
                     dimension=Dimension.VOLUME, stock_unit="ml", track_mode=TrackMode.DERIVED,
                     qty_base=Decimal("10000"))
    latte = SoldItem(tenant_id=tenant.id, provider="demo", provider_item_id="latte", name="Latte")
    db.add_all([milk, latte])
    db.flush()
    db.add(RecipeComponent(tenant_id=tenant.id, sold_item_id=latte.id, stock_item_id=milk.id,
                           qty_base_per_sale=Decimal("200")))
    db.add(InventoryCount(tenant_id=tenant.id, stock_item_id=milk.id, qty_base=Decimal("10000"),
                          source="manual", counted_at=datetime(2026, 11, 1, 23, 0)))
    db.add(SalesDaily(tenant_id=tenant.id, sold_item_id=latte.id, location_id=other.id,
                      dt=date(2026, 11, 2), units=Decimal("10")))
    db.commit()
    refresh_derived_quantities(db, tenant.id, today=date(2026, 11, 3))
    db.commit()
    db.refresh(milk)
    assert milk.qty_base == Decimal("8000")  # 10 lattes sold at the OTHER location took 2 L here


INVOICE = {"supplier": "Northside", "invoice_number": "NS-1", "lines": [
    {"raw_text": "CUPS", "product_name": "Cups", "quantity": 24, "unit": "each",
     "pack_size": None, "unit_price_cents": 10, "line_total_cents": 240}]}


def _provider_item(db, tenant, location, qty="10"):
    item = StockItem(tenant_id=tenant.id, location_id=location.id, slug="cups", name="Cups",
                     dimension=Dimension.COUNT, stock_unit="each", track_mode=TrackMode.PROVIDER,
                     provider_variation_id="VAR1", qty_base=Decimal(qty))
    db.add(item)
    db.commit()
    return item


def test_R3_failed_square_push_on_receipt_is_lost_at_next_sync(db, tenant, location, monkeypatch):
    item = _provider_item(db, tenant, location)
    doc = save_upload(db, tenant.id, location.id, "inv.jpg", b"x", "image/jpeg")
    parse_document(db, doc.id, StubExtractor(INVOICE))
    db.commit()

    import app.orchestration.sync as sync_mod

    def boom(*_a, **_k):
        raise RuntimeError("square 503")

    monkeypatch.setattr(sync_mod, "push_adjustments_to_square", boom)
    line = doc.lines[0]
    result = apply_document(db, doc.id, [LineDecision(line.id, item.id, Decimal("24"))])
    db.commit()
    assert result["pushed_to_pos"] == 0 and doc.status is DocumentStatus.APPLIED
    assert item.qty_base == Decimal("34")
    # Hourly reconciliation reads Square, which never heard of the delivery.
    apply_provider_counts(db, tenant.id, [{"provider_item_id": "VAR1", "provider_location_id": "loc-1",
                                           "quantity": Decimal("10")}])
    db.commit()
    assert item.qty_base == Decimal("10")  # the 24 received units are gone; nothing retries
    assert db.query(InventoryEvent).count() == 0  # invoice receiving writes no audit event at all


def test_R4_concurrent_apply_raises_stock_twice(db, tenant, location):
    """Two requests (double-click) both load the document before either commits."""
    item = StockItem(tenant_id=tenant.id, location_id=location.id, slug="cups", name="Cups",
                     dimension=Dimension.COUNT, stock_unit="each", track_mode=TrackMode.MANUAL,
                     qty_base=Decimal("10"))
    db.add(item)
    db.commit()
    doc = save_upload(db, tenant.id, location.id, "inv2.jpg", b"y", "image/jpeg")
    parse_document(db, doc.id, StubExtractor(INVOICE))
    db.commit()
    line_id, doc_id, item_id = doc.lines[0].id, doc.id, item.id

    s1, s2 = SessionLocal(), SessionLocal()
    try:
        held = [s1.get(Document, doc_id), s2.get(Document, doc_id)]  # both requests see PARSED
        held += [held[0].lines, held[1].lines]
        apply_document(s1, doc_id, [LineDecision(line_id, item_id, Decimal("24"))])
        s1.commit()
        apply_document(s2, doc_id, [LineDecision(line_id, item_id, Decimal("24"))])
        s2.commit()
    finally:
        s1.close(); s2.close()
    db.expire_all()
    assert db.get(StockItem, item_id).qty_base == Decimal("58")  # 10 + 24 + 24
    assert db.query(InventoryCount).filter_by(stock_item_id=item_id, source="receipt").count() == 2


def test_R5_duplicate_inventory_webhook_is_not_deduplicated_by_event_id(db, tenant, location):
    """Absolute counts make a duplicate harmless to the balance, but nothing records the event id."""
    _provider_item(db, tenant, location)
    row = {"provider_item_id": "VAR1", "provider_location_id": "loc-1", "quantity": Decimal("7")}
    apply_provider_counts(db, tenant.id, [row])
    apply_provider_counts(db, tenant.id, [row])
    db.commit()
    assert db.query(StockItem).one().qty_base == Decimal("7")
    assert db.query(WebhookEvent).count() == 0
    assert db.query(InventoryEvent).count() == 0  # POS-driven balance changes leave no movement row


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


def test_R7_invoice_image_is_served_with_the_uploaders_content_type(db, tenant, location):
    from app.main import app
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        r = client.post("/app/receiving", data={"location_id": str(location.id)},
                        files={"photo": ("x.jpg", b"<script>alert(document.cookie)</script>", "text/html")},
                        follow_redirects=False)
        doc_id = int(r.headers["location"].rsplit("/", 1)[-1])
        img = client.get(f"/app/receiving/{doc_id}/image")
        assert img.headers["content-type"].startswith("text/html")
        assert "x-content-type-options" not in {k.lower() for k in img.headers}


def test_R8_forms_have_no_csrf_token_and_login_is_unthrottled(db, tenant):
    from app.main import app
    with TestClient(app) as client:
        page = client.get("/login").text
        assert "csrf" not in page.lower()
        codes = {client.post("/login", data={"email": "owner@rosewood.test", "password": f"wrong-{i}xxxxx"}).status_code
                 for i in range(30)}
        assert codes == {401}  # 30 bad attempts, never locked or throttled
