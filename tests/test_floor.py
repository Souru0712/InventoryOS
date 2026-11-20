"""T2.21: the employee home and the shared-device "logged by" picker (WF-23, PD-14, WST-FR-03)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.items import NewItem, create_item
from app.main import app
from app.models import Membership, Tenant, User, WasteEvent
from app.security import hash_password
from app.waste import ensure_reasons

PASSWORD = "a-good-password"


def _member(db, tenant, email, role):
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(PASSWORD),
        email_verified_at=datetime.now(UTC),
    )
    db.add(user)
    db.flush()
    membership = Membership(tenant_id=tenant.id, user_id=user.id, role=role)
    db.add(membership)
    db.commit()
    return user, membership


@pytest.fixture
def floor(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    milk = create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[location.id],
            dimension="volume",
            display_unit="l",
            quantity="10",
            cost_per_unit="1.50",
        ),
    )
    reason = ensure_reasons(db, org)[0]
    tablet, _ = _member(db, tenant, "tablet@rosewood.test", "employee")
    ana, ana_membership = _member(db, tenant, "ana@rosewood.test", "employee")
    db.commit()
    return milk, reason, tablet, ana, ana_membership


def _signed_in(email):
    client = TestClient(app)
    client.__enter__()
    landed = client.post("/login", data={"email": email, "password": PASSWORD})
    return client, landed


def test_an_employee_lands_on_today_with_only_their_tasks(db, tenant, floor):
    client, landed = _signed_in("tablet@rosewood.test")
    assert landed.url.path == "/app/floor"
    page = landed.text
    for task in ("Log waste", "Count", "Receive a delivery", "Make a batch", "Expiring"):
        assert task in page
    assert "Enter what sold today" not in page  # not allowed until the Owner says so (PD-37)
    assert "$" not in page  # no money for an Employee
    tenant_row = db.get(Tenant, tenant.id)
    tenant_row.employees_enter_sales = True
    db.commit()
    assert "Enter what sold today" in client.get("/app/floor").text
    owner, owner_landed = _signed_in("owner@rosewood.test")
    assert owner_landed.url.path == "/app"


def test_the_person_picked_on_a_shared_device_is_named_on_the_record(db, tenant, location, floor):
    milk, reason, tablet, ana, ana_membership = floor
    client, page = _signed_in("tablet@rosewood.test")
    assert "Who's working?" in page.text
    picked = client.post("/app/floor/person", data={"membership_id": ana_membership.id})
    assert "now name ana@rosewood.test" in picked.text
    client.post(
        "/app/waste",
        data={
            "location_id": location.id,
            "reason_id": reason.id,
            "inventory_item_id": milk.id,
            "quantity": "1",
        },
    )
    client.post("/app/floor/person", data={"membership_id": "0"})  # back to the account itself
    client.post(
        "/app/waste",
        data={
            "location_id": location.id,
            "reason_id": reason.id,
            "inventory_item_id": milk.id,
            "quantity": "1",
        },
    )
    actors = [e.actor_user_id for e in db.scalars(select(WasteEvent).order_by(WasteEvent.id))]
    assert actors == [ana.id, tablet.id]


def test_only_a_shared_floor_account_picks_and_only_members_can_be_picked(db, tenant, floor):
    _milk, _reason, _tablet, _ana, ana_membership = floor
    owner, _ = _signed_in("owner@rosewood.test")
    refused = owner.post("/app/floor/person", data={"membership_id": ana_membership.id})
    assert "Only a shared floor account" in refused.text
    tablet, _ = _signed_in("tablet@rosewood.test")
    assert tablet.post("/app/floor/person", data={"membership_id": "999999"}).status_code == 404
    db.query(Membership).filter_by(id=ana_membership.id).one().status = "revoked"
    db.commit()
    # Someone removed from the business is not named, even if this device had picked them.
    assert "Who's working?" in tablet.get("/app/floor").text
