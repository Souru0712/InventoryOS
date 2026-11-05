"""Paid boxes against the till: nothing held before payment, units sold out of Square when paid, the till
overselling a paid unit, pushes Square missed, packing, refunds, and the Square payment and refund webhooks."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    BoxLineStatus,
    BoxOrder,
    BoxOrderStatus,
    InventoryEvent,
    Location,
    ProviderConnection,
    SnackProfile,
    StockItem,
    Tenant,
    WebhookEvent,
)
from app.orchestration import boxes, customers
from app.providers.square.webhooks import signature_for
from tests.snack_box_support import (  # noqa: F401  (fixtures)
    NOW,
    lines_of,
    open_store,
    pack_all,
    paid_box,
    shopper,
    square,
    web,
)

KEY = "test-signature-key"
MERCHANT = "MERCHANT-BOXES"


def test_nothing_is_held_until_the_box_is_paid(db, tenant, location, square):
    items = open_store(db, tenant, location, square)
    order = boxes.place_order(
        db, tenant.id, shopper(db, tenant), "chaos", use_credit=False, now=NOW
    )
    assert order.status is BoxOrderStatus.PENDING_PAYMENT
    assert lines_of(db, order) == [] and square.moves == []
    assert all(square.counts[i.provider_variation_id] == 10 for i in items)


def test_payment_sells_the_box_units_out_of_square(db, tenant, location, square):
    items = open_store(db, tenant, location, square)
    order = paid_box(db, tenant, shopper(db, tenant))
    lines = lines_of(db, order)
    assert len(lines) == 8 and all(line.count_applied for line in lines)
    assert {move[1:3] for move in square.moves} == {("IN_STOCK", "SOLD")}
    assert all(square.counts[i.provider_variation_id] == 9 and i.qty_base == 9 for i in items)
    events = db.scalars(select(InventoryEvent).where(InventoryEvent.box_order_id == order.id)).all()
    assert len(events) == 8 and {e.action for e in events} == {"box_allocated"}


def test_the_till_selling_a_paid_unit_shorts_the_newest_box_not_the_oldest(
    db, tenant, location, square
):
    items = open_store(db, tenant, location, square)
    orders = [
        paid_box(db, tenant, shopper(db, tenant, f"c{n}@shop.test"), now=NOW + timedelta(minutes=n))
        for n in range(4)
    ]
    gummy = items[0].provider_variation_id
    assert square.counts[gummy] == 6  # 10 on hand, 4 in paid boxes
    square.sell_at_till(gummy, 7)  # the till rings up 7 anyway: Square now says -1

    assert pack_all(db, orders[0]) == []  # the first customer's box is untouched
    assert "sold at the till" in pack_all(db, orders[3])[0]
    short = [line for line in lines_of(db, orders[3]) if line.status is BoxLineStatus.SHORT]
    assert [line.stock_item_id for line in short] == [items[0].id]
    assert square.counts[gummy] == 0  # the short box handed its unit back to the count
    assert boxes.item_positions(db, tenant.id, [items[0].id])[items[0].id].store_available_base == 0
    assert "choose a replacement" in orders[3].attention


def test_a_short_item_is_swapped_for_a_safe_one_and_the_box_packs(db, tenant, location, square):
    open_store(db, tenant, location, square, n_items=10)
    order = paid_box(db, tenant, shopper(db, tenant))
    victim = lines_of(db, order)[0]
    square.sell_at_till(db.get(StockItem, victim.stock_item_id).provider_variation_id, 10)

    assert "sold at the till" in pack_all(db, order)[0]
    short = next(line for line in lines_of(db, order) if line.status is BoxLineStatus.SHORT)
    options = boxes.replacement_options(db, order, short, now=NOW)
    assert options, "an unused boxable item should be able to stand in"
    assert boxes.replace_line(db, order, short, options[0].candidate.stock_item_id, NOW) == []
    assert order.attention is None
    assert pack_all(db, order) == [] and order.status is BoxOrderStatus.PACKED


def test_a_push_square_missed_holds_the_units_back_and_is_retried(db, tenant, location, square):
    items = open_store(db, tenant, location, square)
    square.down = True
    order = paid_box(db, tenant, shopper(db, tenant))
    assert not any(line.count_applied for line in lines_of(db, order))
    assert order.attention.startswith("Square was not told")
    held = boxes.item_positions(db, tenant.id, [items[0].id])[items[0].id]
    assert (held.count_base, held.store_available_base) == (
        10,
        9,
    )  # Square still counts it; walk-ins may not buy it

    square.down = False
    report = boxes.reconcile(db, tenant.id, NOW)
    assert all(line.count_applied for line in lines_of(db, order)) and order.attention is None
    assert square.counts[items[0].provider_variation_id] == 9
    assert not any(row.issues for row in report)


def test_packing_needs_every_item_ticked_and_a_fresh_count(db, tenant, location, square):
    open_store(db, tenant, location, square)
    order = paid_box(db, tenant, shopper(db, tenant))
    ids = [line.id for line in lines_of(db, order)]
    square.unreadable = True
    assert "could not be read" in boxes.pack(db, order, set(ids), NOW)[0]
    square.unreadable = False
    assert "not ticked" in boxes.pack(db, order, set(ids[1:]), NOW)[0]
    assert boxes.pack(db, order, set(ids), NOW) == [] and order.status is BoxOrderStatus.PACKED


def test_a_refund_before_packing_puts_every_unit_back(db, tenant, location, square):
    items = open_store(db, tenant, location, square)
    order = paid_box(db, tenant, shopper(db, tenant))
    assert boxes.refund(db, order, restock=False, now=NOW) == []
    assert order.status is BoxOrderStatus.REFUNDED
    assert all(line.status is BoxLineStatus.RELEASED for line in lines_of(db, order))
    assert all(square.counts[i.provider_variation_id] == 10 for i in items)


def test_a_refund_after_pickup_restocks_only_when_someone_says_so(db, tenant, location, square):
    items = open_store(db, tenant, location, square)
    order = paid_box(db, tenant, shopper(db, tenant))
    pack_all(db, order)
    boxes.pick_up(db, order, NOW)
    boxes.refund(db, order, restock=False, now=NOW)
    assert all(square.counts[i.provider_variation_id] == 9 for i in items)
    assert boxes.restock(db, order, NOW) == []
    assert all(square.counts[i.provider_variation_id] == 10 for i in items)


def test_unpaid_orders_lapse(db, tenant, location, square):
    open_store(db, tenant, location, square)
    order = boxes.place_order(
        db, tenant.id, shopper(db, tenant), "chaos", use_credit=False, now=NOW
    )
    assert boxes.expire_unpaid(db, tenant.id, NOW + timedelta(minutes=59)) == 0
    assert boxes.expire_unpaid(db, tenant.id, NOW + timedelta(minutes=61)) == 1
    assert order.status is BoxOrderStatus.CANCELLED


def test_no_allergy_answer_no_order(db, tenant, location, square):
    open_store(db, tenant, location, square)
    unanswered = customers.find_or_create(db, tenant.id, "new@shop.test", NOW)
    with pytest.raises(boxes.BoxProblem, match="allergies"):
        boxes.place_order(db, tenant.id, unanswered, "chaos", now=NOW)


def test_a_box_that_must_be_spicy_is_refused_to_someone_who_wants_nothing_spicy(
    db, tenant, location, square
):
    open_store(db, tenant, location, square)
    customer = shopper(db, tenant)
    customers.update_preferences(db, customer, spice="none", now=NOW)
    with pytest.raises(boxes.BoxProblem, match="something spicy"):
        boxes.place_order(db, tenant.id, customer, "chaos", now=NOW)
    assert (
        boxes.place_order(db, tenant.id, customer, "world", use_credit=False, now=NOW).box_type
        == "world"
    )


def test_a_declared_allergen_never_reaches_the_box(db, tenant, location, square):
    items = open_store(db, tenant, location, square, n_items=12)
    db.scalars(
        select(SnackProfile).where(SnackProfile.stock_item_id == items[0].id)
    ).one().allergens = "milk"
    order = paid_box(db, tenant, shopper(db, tenant, allergens=frozenset({"milk"})))
    assert len(lines_of(db, order)) == 8
    assert items[0].id not in {line.stock_item_id for line in lines_of(db, order)}


# --- Square webhooks -------------------------------------------------------------------------------


@pytest.fixture
def signed(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "square_webhook_signature_key", KEY, raising=False)
    monkeypatch.setattr(settings, "app_base_url", "https://stockpulse.test", raising=False)


def post(client, payload: dict):
    body = json.dumps(payload).encode()
    signature = signature_for("https://stockpulse.test/webhooks/square", body, KEY)
    return client.post(
        "/webhooks/square",
        content=body,
        headers={"x-square-hmacsha256-signature": signature, "content-type": "application/json"},
    )


def payment(event_id: str, amount: int, status: str = "COMPLETED") -> dict:
    return {
        "merchant_id": MERCHANT,
        "type": "payment.updated",
        "event_id": event_id,
        "data": {
            "type": "payment",
            "id": "PAY-1",
            "object": {
                "payment": {
                    "id": "PAY-1",
                    "status": status,
                    "order_id": "SQ-ORDER-1",
                    "amount_money": {"amount": amount, "currency": "USD"},
                }
            },
        },
    }


def refund_event(event_id: str, amount: int) -> dict:
    return {
        "merchant_id": MERCHANT,
        "type": "refund.updated",
        "event_id": event_id,
        "data": {
            "type": "refund",
            "id": "REF-1",
            "object": {
                "refund": {
                    "id": "REF-1",
                    "status": "COMPLETED",
                    "payment_id": "PAY-1",
                    "order_id": "SQ-ORDER-1",
                    "amount_money": {"amount": amount, "currency": "USD"},
                }
            },
        },
    }


def connected_order(square_fake, paid: bool = False) -> int:
    with SessionLocal() as db:
        tenant = Tenant(name="Snack Shop")
        db.add(tenant)
        db.flush()
        db.add(
            ProviderConnection(
                tenant_id=tenant.id,
                provider="square",
                merchant_id=MERCHANT,
                access_token_enc="x",
                status="active",
            )
        )
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id="L1", name="Main"
        )
        db.add(location)
        db.flush()
        open_store(db, tenant, location, square_fake)
        order = boxes.place_order(
            db, tenant.id, shopper(db, tenant), "chaos", use_credit=False, now=NOW
        )
        order.square_order_id = "SQ-ORDER-1"
        if paid:
            boxes.confirm_payment(db, order, "square", "PAY-1", NOW)
            assert pack_all(db, order) == []
        db.commit()
        return order.id


def test_a_square_payment_confirms_the_box_exactly_once(web, square, signed):
    order_id = connected_order(square)
    assert post(web, payment("EVT-1", 2499)).json() == {"box_order": order_id, "status": "paid"}
    assert post(web, payment("EVT-1", 2499)).json() == {"ignored": "duplicate event"}
    assert (
        post(web, payment("EVT-2", 2499)).json()["status"] == "paid"
    )  # a second delivery of the same payment
    with SessionLocal() as db:
        assert len(lines_of(db, db.get(BoxOrder, order_id))) == 8
        assert len(db.scalars(select(WebhookEvent)).all()) == 2


def test_an_underpaid_square_payment_is_flagged_not_confirmed(web, square, signed):
    order_id = connected_order(square)
    assert post(web, payment("EVT-1", 1000)).json() == {"ignored": "underpaid"}
    with SessionLocal() as db:
        order = db.get(BoxOrder, order_id)
        assert order.status is BoxOrderStatus.PENDING_PAYMENT and "were due" in order.attention


def test_a_square_refund_after_packing_leaves_restocking_to_a_person(web, square, signed):
    order_id = connected_order(square, paid=True)
    assert post(web, refund_event("EVT-9", 2499)).json()["status"] == "refunded"
    with SessionLocal() as db:
        order = db.get(BoxOrder, order_id)
        assert "Restock" in order.attention
        assert all(line.status is BoxLineStatus.PACKED for line in lines_of(db, order))
    assert all(count == 9 for count in square.counts.values())
