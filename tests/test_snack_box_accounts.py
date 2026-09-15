"""Shopper accounts: one-time links, a shopper at one store is nobody at another, preferences are audited,
and the whole loop from first box to a second box that listened."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import (
    BoxOrder,
    Customer,
    CustomerLoginToken,
    Location,
    PreferenceChange,
    ProductPreferenceState,
    StockItem,
    Tenant,
)
from app.orchestration import boxes, customers
from tests.snack_box_support import (  # noqa: F401  (fixtures)
    NOW,
    lines_of,
    open_store,
    outbox,
    pack_all,
    paid_box,
    shopper,
    sign_in,
    square,
    web,
)


def make_store(name: str = "Snack Shop") -> int:
    with SessionLocal() as db:
        tenant = Tenant(name=name)
        db.add(tenant)
        db.flush()
        db.add(Location(tenant_id=tenant.id, provider="demo", provider_location_id=f"L-{name}", name="Main"))
        boxes.box_settings(db, tenant.id).is_open = True
        db.commit()
        return tenant.id


# --- signing in -----------------------------------------------------------------------------------

def test_a_sign_in_link_works_once(web, outbox):
    store = make_store()
    assert web.get(f"/shop/{store}/me", follow_redirects=False).status_code == 303
    response, token = sign_in(web, outbox, store, "Maya@Shop.test")
    assert response.status_code == 303 and response.headers["location"].endswith("/me/preferences?welcome=1")
    assert web.get(f"/shop/{store}/me/preferences").status_code == 200
    web.get(f"/shop/{store}/logout")
    assert web.post(f"/shop/{store}/auth", data={"token": token}).status_code == 400


def test_opening_the_link_does_not_spend_it(web, outbox):
    # Mail scanners fetch links; only pressing the button signs anyone in.
    store = make_store()
    web.post(f"/shop/{store}/login", data={"email": "maya@shop.test"})
    token = outbox[-1]["text"].split("token=", 1)[1].split()[0]
    assert web.get(f"/shop/{store}/auth?token={token}").status_code == 200
    assert web.get(f"/shop/{store}/me", follow_redirects=False).status_code == 303
    assert web.post(f"/shop/{store}/auth", data={"token": token}, follow_redirects=False).status_code == 303


def test_links_are_stored_hashed_and_expire(db, tenant):
    customer = customers.find_or_create(db, tenant.id, "maya@shop.test", NOW)
    raw = customers.issue_login_link(db, customer, NOW).split("token=", 1)[1]
    assert raw not in {t.token_hash for t in db.scalars(select(CustomerLoginToken))}
    assert customers.redeem_login_token(db, tenant.id, raw, NOW + timedelta(minutes=21)) is None
    assert customers.redeem_login_token(db, tenant.id, raw, NOW + timedelta(minutes=5)) == customer


def test_link_requests_are_rate_limited(db, tenant):
    customer = customers.find_or_create(db, tenant.id, "maya@shop.test", NOW)
    links = [customers.issue_login_link(db, customer, NOW) for _ in range(6)]
    assert links[4] is not None and links[5] is None


def test_a_link_from_one_store_does_not_open_another(db, tenant):
    other = Tenant(name="Other")
    db.add(other)
    db.flush()
    customer = customers.find_or_create(db, tenant.id, "maya@shop.test", NOW)
    raw = customers.issue_login_link(db, customer, NOW).split("token=", 1)[1]
    assert customers.redeem_login_token(db, other.id, raw, NOW) is None


# --- who can see what ---------------------------------------------------------------------------

def test_one_customer_cannot_open_another_customers_box(web, outbox):
    store = make_store()
    with SessionLocal() as db:
        other = shopper(db, db.get(Tenant, store), "other@shop.test")
        order = boxes.place_order(db, store, other, "chaos", use_credit=False)
        db.commit()
        order_id = order.id
    sign_in(web, outbox, store, "maya@shop.test")
    assert web.get(f"/shop/{store}/me/orders/{order_id}").status_code == 404
    assert web.post(f"/shop/{store}/me/orders/{order_id}/cancel").status_code == 404
    assert web.get(f"/shop/{store}/me/orders/{order_id}/review").status_code == 404
    with SessionLocal() as db:
        assert db.get(BoxOrder, order_id).cancelled_at is None


def test_a_shopper_at_one_store_is_nobody_at_another(web, outbox):
    first, second = make_store("A"), make_store("B")
    sign_in(web, outbox, first, "maya@shop.test")
    assert web.get(f"/shop/{first}/me").status_code == 200
    assert web.get(f"/shop/{second}/me", follow_redirects=False).status_code == 303


def test_a_store_login_is_not_a_shopper_login_and_back(web, outbox):
    web.post("/signup", data={"business_name": "Snack Shop", "email": "owner@shop.test", "password": "a-good-password"})
    with SessionLocal() as db:
        store = db.scalars(select(Tenant)).one().id
    assert web.get(f"/shop/{store}/me", follow_redirects=False).status_code == 303
    web.get("/logout")
    with SessionLocal() as db:
        db.add(Location(tenant_id=store, provider="demo", provider_location_id="L1", name="Main"))
        boxes.box_settings(db, store).is_open = True
        db.commit()
    sign_in(web, outbox, store, "maya@shop.test")
    response = web.get("/app/boxes", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_a_workspace_without_snack_boxes_has_no_shop(web, outbox):
    web.post("/signup", data={"business_name": "Cafe", "email": "owner@cafe.test", "password": "a-good-password"})
    with SessionLocal() as db:
        store = db.scalars(select(Tenant)).one().id
    assert web.get(f"/shop/{store}").status_code == 404
    assert web.post(f"/shop/{store}/login", data={"email": "stranger@mail.test"}).status_code == 404
    assert outbox == []


def test_a_closed_shop_mails_its_own_customers_only(web, outbox):
    store = make_store()
    with SessionLocal() as db:
        shopper(db, db.get(Tenant, store), "regular@shop.test")
        boxes.box_settings(db, store).is_open = False
        db.commit()
    web.post(f"/shop/{store}/login", data={"email": "stranger@mail.test"})
    assert outbox == []
    web.post(f"/shop/{store}/login", data={"email": "regular@shop.test"})
    assert [message["to"] for message in outbox] == ["regular@shop.test"]
    with SessionLocal() as db:
        assert db.scalars(select(Customer).where(Customer.email == "stranger@mail.test")).first() is None


# --- preferences ---------------------------------------------------------------------------------

def test_allergies_are_answered_not_assumed(web, outbox):
    store = make_store()
    sign_in(web, outbox, store, "maya@shop.test")
    response = web.post(f"/shop/{store}/me/preferences", data={"spice": "medium", "novelty": "balanced"})
    assert response.status_code == 400 and "None of these" in response.text
    response = web.post(f"/shop/{store}/me/orders", data={"box_type": "chaos"})
    assert response.status_code == 400 and "allergies" in response.text
    response = web.post(f"/shop/{store}/me/preferences", follow_redirects=False,
                        data={"allergen_none": "on", "spice": "medium", "novelty": "balanced"})
    assert response.status_code == 303


def test_preference_changes_are_audited_and_never_again_can_be_undone(db, tenant, location, square):
    items = open_store(db, tenant, location, square)
    customer = shopper(db, tenant)
    customers.update_preferences(db, customer, spice="hot", now=NOW)
    customers.set_product_preference(db, customer, items[0].id, ProductPreferenceState.NEVER_AGAIN, now=NOW)
    customers.set_product_preference(db, customer, items[0].id, None, now=NOW)
    changes = [(c.field, c.old_value, c.new_value)
               for c in db.scalars(select(PreferenceChange).order_by(PreferenceChange.id))]
    assert ("allergens", None, "") in changes and ("spice", "medium", "hot") in changes
    assert changes[-2:] == [(f"product:{items[0].id}", None, "never_again"), (f"product:{items[0].id}", "never_again", None)]
    assert items[0].id not in customers.taste_for(db, customer).never_again


def test_closing_an_account_keeps_its_orders_and_signing_in_reopens_it(db, tenant, location, square):
    open_store(db, tenant, location, square)
    customer = shopper(db, tenant)
    order = paid_box(db, tenant, customer)
    customers.deactivate(db, customer, NOW)
    assert not customer.is_active and db.get(BoxOrder, order.id) is not None
    raw = customers.issue_login_link(db, customer, NOW).split("token=", 1)[1]
    assert customers.redeem_login_token(db, tenant.id, raw, NOW).is_active


def test_the_survey_records_never_again_and_a_changed_answer_takes_it_back(db, tenant, location, square):
    open_store(db, tenant, location, square)
    customer = shopper(db, tenant)
    order = paid_box(db, tenant, customer)
    with pytest.raises(customers.ReviewProblem, match="picked it up"):
        customers.submit_review(db, customer, order, {}, now=NOW)

    assert pack_all(db, order) == []
    boxes.pick_up(db, order, NOW)
    first = lines_of(db, order)[0].stock_item_id
    customers.submit_review(db, customer, order, {first: ("never_again", True, "")}, 4, "yes", NOW)
    assert first in customers.taste_for(db, customer).never_again
    assert first not in customers.taste_for(db, customer).repeat  # never again wins the contradiction
    customers.submit_review(db, customer, order, {first: ("liked", False, "")}, now=NOW)
    assert first not in customers.taste_for(db, customer).never_again


def test_the_whole_loop_the_second_box_listens(db, tenant, location, square):
    open_store(db, tenant, location, square, n_items=16)
    customer = shopper(db, tenant)

    first = paid_box(db, tenant, customer)
    assert pack_all(db, first) == []
    boxes.pick_up(db, first, NOW)
    got = [line.stock_item_id for line in lines_of(db, first)]
    never, again = got[0], got[1]
    customers.submit_review(db, customer, first, {never: ("never_again", False, ""), again: ("loved", True, "")}, now=NOW)
    avoided = db.get(StockItem, got[2]).category
    customers.update_preferences(db, customer, avoided_categories=frozenset({avoided}), now=NOW)

    second = paid_box(db, tenant, customer, now=NOW + timedelta(days=30))
    lines = lines_of(db, second)
    picked = {line.stock_item_id for line in lines}
    assert len(lines) == 8
    assert never not in picked
    assert again in picked
    assert not picked & (set(got) - {again})  # nothing else from the last box comes back this soon
    for line in lines:
        if db.get(StockItem, line.stock_item_id).category.lower() == avoided.lower():
            assert "avoids" in line.why
