"""T3.07: refunds, voids and discounts, WF-17's case table (POS-FR-08, -09, -26, DR-26, PD-10,
AC-POS-04, AC-POS-05). The POS is a fake adapter returning normalized orders."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.ledger.posting import balance
from app.main import app
from app.models import SalesReturn, Tenant
from app.providers.base import NormalizedOrderLine, NormalizedRefund, RefundLine
from app.providers.square.normalize import to_order
from app.sales import sales_money
from tests.accounts import confirm_email
from tests.test_order_ingestion import NOW, _read, _sales, latte, order, shop  # noqa: F401

D = Decimal
TODAY = NOW.date()


def refund(ref, original, amount, *lines, status="completed", at=NOW):
    return NormalizedRefund(
        external_refund_id=ref,
        external_order_id=original,
        lines=tuple(RefundLine(uid, D(qty)) for uid, qty in lines),
        amount_cents=amount,
        status=status,
        occurred_at=at,
    )


def returns(db) -> list[SalesReturn]:
    return list(db.scalars(select(SalesReturn).order_by(SalesReturn.id)))


def test_ac_pos_04_an_order_cancelled_before_completion_uses_nothing(db, location, shop):
    org, pos, _connection, milk, _shot = shop
    pos.orders = [order("o-1", latte("A", 2), state="open")]
    _read(db, org)
    pos.orders = [order("o-1", latte("A", 2), state="canceled", version=2)]
    _read(db, org, NOW + timedelta(minutes=5))
    assert _sales(db, "order:o-1") == []
    assert balance(db, org, location.id, milk.id) == 0


def test_a_completed_order_voided_at_the_pos_gives_back_what_it_took(db, location, shop):
    org, pos, _connection, milk, _shot = shop
    pos.orders = [order("o-1", latte("A", 2))]
    _read(db, org)
    assert balance(db, org, location.id, milk.id) == D(-400)
    pos.orders = [order("o-1", latte("A", 2), state="canceled", version=2)]
    assert _read(db, org, NOW + timedelta(minutes=5))["voided"] == 1
    assert [s.status for s in _sales(db, "order:o-1")] == ["voided"]
    assert balance(db, org, location.id, milk.id) == 0, "void = not made (A-07)"
    assert sales_money(db, org, TODAY, TODAY).net_cents == 0


def test_ac_pos_05_a_refund_is_recorded_linked_lowers_net_sales_and_restocks_nothing(
    db, location, shop
):
    org, pos, _connection, milk, _shot = shop
    pos.orders = [order("o-1", latte("A", 2))]
    _read(db, org)
    paid = [order("o-1", latte("A", 2), version=2)]
    pos.orders = [replace(paid[0], refunds=(refund("r-1", "o-1", 450, ("A", 1)),))]
    assert _read(db, org, NOW + timedelta(minutes=5))["refunds"] == 1

    [back] = returns(db)
    [sale] = _sales(db, "order:o-1")
    assert (back.sales_transaction_id, back.amount_cents, back.status) == (sale.id, 450, "recorded")
    [line] = back.lines
    assert (line.sales_transaction_line_id, line.quantity, line.amount_cents) == (
        sale.lines[0].id,
        D(1),
        450,  # half of the line's 900 net
    )
    money = sales_money(db, org, TODAY, TODAY)
    assert (money.gross_cents, money.refunded_cents, money.net_cents) == (900, 450, 450)
    assert balance(db, org, location.id, milk.id) == D(-400), "a refund never restocks (DR-26)"

    # The refund comes again with the order's next version: still one return.
    pos.orders = [replace(pos.orders[0], version=3)]
    _read(db, org, NOW + timedelta(minutes=10))
    assert len(returns(db)) == 1


def test_a_refund_that_arrives_before_its_order_is_linked_when_the_order_does(db, shop):
    org, pos, _connection, _milk, _shot = shop
    give_back = order("ret-1", state="completed")  # Square's return order: no lines of its own
    pos.orders = [replace(give_back, refunds=(refund("r-1", "o-1", 450, ("A", 1)),))]
    _read(db, org)
    [waiting] = returns(db)
    assert waiting.sales_transaction_id is None and waiting.lines[0].line_key == "line:A"
    assert sales_money(db, org, TODAY, TODAY).refunded_cents == 450

    pos.orders = [order("o-1", latte("A", 2))]
    _read(db, org, NOW + timedelta(minutes=5))
    db.refresh(waiting)
    [sale] = _sales(db, "order:o-1")
    assert waiting.sales_transaction_id == sale.id
    assert waiting.lines[0].sales_transaction_line_id == sale.lines[0].id


def test_a_pending_refund_waits_until_money_goes_back(db, shop):
    org, pos, _connection, _milk, _shot = shop
    base = order("o-1", latte("A", 1))
    pos.orders = [replace(base, refunds=(refund("r-1", "o-1", 450, status="pending"),))]
    _read(db, org)
    assert returns(db) == []
    pos.orders = [replace(base, version=2, refunds=(refund("r-1", "o-1", 450),))]
    _read(db, org, NOW + timedelta(minutes=5))
    assert len(returns(db)) == 1


def test_a_discount_or_comp_changes_money_never_what_was_used(db, location, shop):
    org, pos, _connection, milk, _shot = shop
    comped = NormalizedOrderLine("A", "VAR-LATTE", "Latte", D(2), gross_cents=900, net_cents=0)
    pos.orders = [order("o-1", comped)]
    _read(db, org)
    assert balance(db, org, location.id, milk.id) == D(-400)
    assert sales_money(db, org, TODAY, TODAY).net_cents == 0


def test_square_returns_name_their_original_order_and_two_refunds_keep_to_amounts():
    raw = {
        "id": "ret-1",
        "state": "COMPLETED",
        "location_id": "L1",
        "created_at": "2026-11-20T10:00:00Z",
        "returns": [
            {
                "uid": "x",
                "source_order_id": "o-1",
                "return_line_items": [{"source_line_item_uid": "A", "quantity": "1"}],
            }
        ],
        "refunds": [
            {"id": "r-1", "status": "COMPLETED", "amount_money": {"amount": 450}},
        ],
    }
    [one] = to_order(raw).refunds
    assert (one.external_order_id, one.lines[0].line_uid) == ("o-1", "A")
    raw["refunds"].append({"id": "r-2", "status": "PENDING", "amount_money": {"amount": 100}})
    raw["refunds"][0]["created_at"] = "2026-11-20T10:05:00Z"
    first, second = to_order(raw).refunds
    assert first.lines == () and second.lines == (), "which return each paid for is unknown"
    assert first.occurred_at is not None and second.status == "pending"


def test_the_connection_page_counts_refunds_and_shows_money_only_to_cost_roles(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(get_settings(), "square_app_id", "app-id", raising=False)
    with TestClient(app) as client:
        client.post(
            "/signup",
            data={
                "business_name": "Corner",
                "email": "o@corner.test",
                "password": "a-good-password",
            },
        )
        confirm_email("o@corner.test")
        from app.access import OrgContext
        from app.models import Location, ProviderConnection
        from app.sales import record_pos_refund

        with SessionLocal() as db:
            tenant = db.scalars(select(Tenant)).one()
            place = Location(tenant_id=tenant.id, name="Main", timezone="UTC")
            db.add_all(
                [
                    place,
                    ProviderConnection(
                        tenant_id=tenant.id,
                        provider="square",
                        merchant_id="M-1",
                        access_token_enc="enc",
                    ),
                ]
            )
            db.flush()
            record_pos_refund(
                db,
                OrgContext.system(tenant.id),
                "square",
                place.id,
                reference="refund:r-1",
                original_reference="order:o-9",
                occurred_at=NOW,
                amount_cents=1250,
            )
            db.commit()
        page = client.get("/app/connection").text
    assert "1 refund recorded (12.50)" in page and "1 is not matched to an order yet" in page
