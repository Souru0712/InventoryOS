"""Every snack box page renders for the store and for the shopper, the forms work end to end,
and a store only ever sees its own boxes."""
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BoxLine, BoxOrder, BoxOrderStatus, BoxSettings, Location, SnackProfile, Tenant
from tests.snack_box_support import (  # noqa: F401  (fixtures)
    lines_of,
    open_store,
    outbox,
    paid_box,
    shopper,
    sign_in,
    square,
    web,
)

OWNER = {"business_name": "Snack Shop", "email": "owner@shop.test", "password": "a-good-password"}


def seeded(square_fake) -> tuple[int, int, list[int]]:
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        location = Location(tenant_id=tenant.id, provider="demo", provider_location_id="L1", name="Main")
        db.add(location)
        db.flush()
        open_store(db, tenant, location, square_fake, n_items=10)
        order = paid_box(db, tenant, shopper(db, tenant))
        db.commit()
        return tenant.id, order.id, [line.id for line in lines_of(db, order)]


def test_the_store_pages_render_and_the_box_can_be_run_from_them(web, square, outbox):
    web.post("/signup", data=OWNER)
    store, order_id, line_ids = seeded(square)

    for path in ["/app/boxes", f"/app/boxes/{order_id}", f"/app/boxes/{order_id}?swap={line_ids[0]}",
                 "/app/boxes/stock", "/app/boxes/snacks", "/app/boxes/settings", "/app/boxes/referrals"]:
        assert web.get(path).status_code == 200, path

    assert web.post(f"/app/boxes/{order_id}/pack", data={"line": [str(i) for i in line_ids]}).status_code == 200
    assert web.post(f"/app/boxes/{order_id}/picked-up").status_code == 200
    with SessionLocal() as db:
        assert db.get(BoxOrder, order_id).status is BoxOrderStatus.PICKED_UP

    response = web.post("/app/boxes/settings", data={
        "is_open": "on", "price_sweet_cents": "22.00", "price_chaos_cents": "24.99", "price_world_cents": "26.50",
        "min_items": "8", "max_items": "12", "target_cogs_cents": "8.00", "packaging_cents": "1.25",
        "payment_fee_pct": "2.9", "payment_fee_fixed_cents": "0.30", "min_contribution_cents": "10.00",
        "min_days_after_pickup": "14", "pickup_after_days": "2", "referral_reward_cents": "5.00",
        "referral_discount_cents": "5.00", "payment_window_minutes": "60",
    })
    assert response.status_code == 200 and "Saved" in response.text
    with SessionLocal() as db:
        settings = db.scalars(select(BoxSettings)).one()
        assert (settings.price_sweet_cents, settings.payment_fee_bps) == (2200, 290)

    item_id = lines_of_order_item(order_id)
    response = web.post(f"/app/boxes/snacks/{item_id}", data={"boxable": "on", "allergens_state": "unknown"})
    assert "allergens from the label" in response.text
    with SessionLocal() as db:
        assert db.scalars(select(SnackProfile).where(SnackProfile.stock_item_id == item_id)).one().allergens == ""

    sign_in(web, outbox, store, "maya@shop.test")
    for path in [f"/shop/{store}", f"/shop/{store}/me", f"/shop/{store}/me/orders", f"/shop/{store}/me/orders/{order_id}",
                 f"/shop/{store}/me/orders/{order_id}/review", f"/shop/{store}/me/preferences",
                 f"/shop/{store}/me/referrals"]:
        assert web.get(path).status_code == 200, path
    response = web.post(f"/shop/{store}/me/orders/{order_id}/review",
                        data={f"rating_{item_id}": "loved", f"again_{item_id}": "on", "overall": "5"})
    assert response.status_code == 200 and "next box" in response.text

    web.get("/logout")
    web.post("/signup", data={**OWNER, "business_name": "Another Shop", "email": "other@shop.test"})
    assert web.get(f"/app/boxes/{order_id}").status_code == 404
    assert web.post(f"/app/boxes/{order_id}/refund").status_code == 404


def lines_of_order_item(order_id: int) -> int:
    with SessionLocal() as db:
        return db.scalars(select(BoxLine.stock_item_id).where(BoxLine.box_order_id == order_id).order_by(BoxLine.id)).first()
