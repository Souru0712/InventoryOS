"""T1.05: a person reaches businesses through memberships, and switches between them."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import OrgContext, members
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Location,
    Membership,
    MembershipLocation,
    StockItem,
    Tenant,
    User,
)
from app.security import hash_password
from app.units import Dimension


def _business_with_item(db, name: str, item: str) -> Tenant:
    tenant = Tenant(name=name)
    db.add(tenant)
    db.flush()
    location = Location(
        tenant_id=tenant.id, provider=None, provider_location_id=None, name=f"{name} Main"
    )
    db.add(location)
    db.flush()
    db.add(
        StockItem(
            tenant_id=tenant.id,
            location_id=location.id,
            slug=item.lower().replace(" ", "-"),
            name=item,
            dimension=Dimension.COUNT,
            stock_unit="each",
            qty_base=Decimal("5"),
        )
    )
    db.flush()
    return tenant


@pytest.fixture
def two_businesses():
    """Someone who owns Rosewood (signed up) and also manages Juniper (a membership)."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "sam@rosewood.test",
                "password": "a-good-password",
            },
        )
        with SessionLocal() as db:
            rosewood = db.scalars(select(Tenant).where(Tenant.name == "Rosewood")).one()
            location = Location(
                tenant_id=rosewood.id,
                provider=None,
                provider_location_id=None,
                name="Rosewood Main",
            )
            db.add(location)
            db.flush()
            db.add(
                StockItem(
                    tenant_id=rosewood.id,
                    location_id=location.id,
                    slug="rosewood-beans",
                    name="Rosewood beans",
                    dimension=Dimension.COUNT,
                    stock_unit="each",
                )
            )
            juniper = _business_with_item(db, "Juniper", "Juniper oats")
            sam = db.scalars(select(User).where(User.email == "sam@rosewood.test")).one()
            joined = Membership(tenant_id=juniper.id, user_id=sam.id, role="manager")
            db.add(joined)
            db.commit()
            ids = {"rosewood": rosewood.id, "juniper": juniper.id, "sam": sam.id}
            ids["juniper_membership"] = joined.id
            ids["rosewood_membership"] = db.scalars(
                select(Membership.id).where(Membership.tenant_id == rosewood.id)
            ).one()
        yield client, ids


# --- one person, several businesses (PA3 inverted) ----------------------------------------------


def test_one_person_can_belong_to_two_businesses_but_only_once_to_each(db, tenant):
    """Probe PA3 inverted: a user was bound to one tenant; memberships make it many."""
    owner = db.scalars(select(User).where(User.email == "owner@rosewood.test")).one()
    other = Tenant(name="Second Shop")
    db.add(other)
    db.flush()
    db.add(Membership(tenant_id=other.id, user_id=owner.id, role="administrator"))
    db.commit()
    assert {
        m.tenant_id for m in db.scalars(select(Membership).where(Membership.user_id == owner.id))
    } == {
        tenant.id,
        other.id,
    }

    db.add(Membership(tenant_id=other.id, user_id=owner.id, role="employee"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@pytest.mark.parametrize(("field", "value"), [("role", "superuser"), ("status", "paused")])
def test_a_membership_has_a_known_role_and_status(db, tenant, field, value):
    owner = db.scalars(select(User)).one()
    other = Tenant(name="Second Shop")
    db.add(other)
    db.flush()
    values = {"tenant_id": other.id, "user_id": owner.id, "role": "employee", field: value}
    db.add(Membership(**values))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_signing_up_makes_the_person_the_owner(two_businesses):
    _client, ids = two_businesses
    with SessionLocal() as db:
        owned = db.get(Membership, ids["rosewood_membership"])
        assert (owned.role, owned.status, owned.all_locations) == ("owner", "active", True)


# --- switching (AC-STF-07) ----------------------------------------------------------------------


def test_after_switching_only_the_selected_business_is_seen(two_businesses):
    client, ids = two_businesses

    first = client.get("/app")
    assert "Rosewood beans" in first.text and "Juniper oats" not in first.text
    assert 'action="/app/organizations/switch"' in first.text  # the switcher is offered

    switched = client.post(
        "/app/organizations/switch",
        data={"membership_id": ids["juniper_membership"]},
        follow_redirects=False,
    )
    assert switched.status_code == 303
    page = client.get("/app")
    assert "Juniper oats" in page.text and "Rosewood beans" not in page.text
    assert "<h1>Juniper</h1>" in page.text

    client.post("/app/organizations/switch", data={"membership_id": ids["rosewood_membership"]})
    assert "Rosewood beans" in client.get("/app").text


def test_writes_land_in_the_selected_business(two_businesses):
    client, ids = two_businesses
    client.post("/app/organizations/switch", data={"membership_id": ids["juniper_membership"]})
    with SessionLocal() as db:
        juniper_location = db.scalars(
            select(Location).where(Location.tenant_id == ids["juniper"])
        ).one()
    client.post(
        "/app/catalog/new",
        data={
            "name": "Oat milk",
            "location_ids": [juniper_location.id],
            "display_unit": "each",
        },
    )
    with SessionLocal() as db:
        made = db.scalars(select(StockItem).where(StockItem.name == "Oat milk")).one()
        assert made.tenant_id == ids["juniper"]


def test_a_business_the_person_does_not_belong_to_cannot_be_chosen(two_businesses):
    client, ids = two_businesses
    with SessionLocal() as db:
        stranger = _business_with_item(db, "Stranger", "Stranger stock")
        their_owner = User(
            tenant_id=stranger.id, email="x@stranger.test", password_hash=hash_password("p" * 10)
        )
        db.add(their_owner)
        db.flush()
        theirs = Membership(tenant_id=stranger.id, user_id=their_owner.id, role="owner")
        db.add(theirs)
        db.commit()
        their_membership = theirs.id

    refused = client.post("/app/organizations/switch", data={"membership_id": their_membership})
    assert refused.status_code == 404
    assert "Rosewood beans" in client.get("/app").text


def test_a_revoked_membership_gives_no_access(two_businesses):
    client, ids = two_businesses
    client.post("/app/organizations/switch", data={"membership_id": ids["juniper_membership"]})
    with SessionLocal() as db:
        db.get(Membership, ids["juniper_membership"]).status = "revoked"
        db.commit()

    # The next request falls back to a business the person still belongs to.
    page = client.get("/app")
    assert "Rosewood beans" in page.text and "Juniper oats" not in page.text
    assert 'action="/app/organizations/switch"' not in page.text  # one business left
    refused = client.post(
        "/app/organizations/switch", data={"membership_id": ids["juniper_membership"]}
    )
    assert refused.status_code == 404

    with SessionLocal() as db:
        db.get(Membership, ids["rosewood_membership"]).status = "revoked"
        db.commit()
    assert client.get("/app").status_code == 403


def test_signing_in_does_not_inherit_the_last_persons_choice(two_businesses):
    client, ids = two_businesses
    client.post("/app/organizations/switch", data={"membership_id": ids["juniper_membership"]})
    client.post("/logout")
    client.post("/login", data={"email": "sam@rosewood.test", "password": "a-good-password"})
    assert "Rosewood beans" in client.get("/app").text  # the first business, not the last


# --- what a membership carries into the context -------------------------------------------------


def test_a_location_scoped_membership_limits_the_context(db, tenant, location):
    owner = db.scalars(select(User)).one()
    other = Tenant(name="Second Shop")
    db.add(other)
    db.flush()
    second = Location(tenant_id=other.id, provider=None, provider_location_id=None, name="Pier")
    db.add(second)
    db.flush()
    scoped = Membership(tenant_id=other.id, user_id=owner.id, role="employee", all_locations=False)
    scoped.locations.append(MembershipLocation(tenant_id=other.id, location_id=second.id))
    db.add(scoped)
    db.commit()

    org = OrgContext.for_membership(scoped)
    assert (org.tenant_id, org.user_id, org.location_ids) == (
        other.id,
        owner.id,
        frozenset({second.id}),
    )


def test_alerts_and_reports_go_to_the_business_members(db, tenant):
    """Users are global: a business reaches the people with an active membership in it."""
    owner = db.scalars(select(User)).one()
    other = Tenant(name="Second Shop")
    db.add(other)
    db.flush()
    db.add(Membership(tenant_id=other.id, user_id=owner.id, role="manager"))
    leaver = User(tenant_id=other.id, email="gone@second.test", password_hash="x")
    db.add(leaver)
    db.flush()
    db.add(Membership(tenant_id=other.id, user_id=leaver.id, role="employee", status="revoked"))
    db.commit()

    assert [user.email for user in members(db, OrgContext.system(other.id))] == [owner.email]
    assert [user.email for user in members(db, OrgContext.system(tenant.id))] == [owner.email]


def test_the_session_remembers_the_choice(two_businesses):
    client, ids = two_businesses
    client.post("/app/organizations/switch", data={"membership_id": ids["juniper_membership"]})
    assert client.get("/app/settings").status_code == 200
    assert "Juniper oats" in client.get("/app").text
