"""T1.12: the business audit history (DR-23, SEC-14, ADM-FR-11, AC-ADM-04)."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext, UnscopedAccess, update_scoped
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuditEvent, Location, Membership, MembershipLocation, Tenant, User
from app.orchestration.stripe_events import handle_event
from app.security import hash_password
from tests.accounts import confirm_email

OWNER = {"email": "owner@corner.test", "password": "a-good-password"}
PRO_PRICE = "price_pro_59"


def browser(ip: str = "203.0.113.5") -> TestClient:
    client = TestClient(app, client=(ip, 50000))
    client.__enter__()
    return client


@pytest.fixture
def corner():
    """Corner Cafe with two locations, its owner signed in; no history yet."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    owner = browser()
    owner.post("/signup", data={"business_name": "Corner Cafe", **OWNER})
    confirm_email(OWNER["email"])
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        for name in ("Main St", "Harbor"):
            db.add(Location(tenant_id=tenant.id, name=name, timezone="America/New_York"))
        db.commit()
    clients = [owner]
    yield owner, clients
    for client in clients:
        client.__exit__(None, None, None)


def events(action_prefix: str = "") -> list[AuditEvent]:
    with SessionLocal() as db:
        query = select(AuditEvent).order_by(AuditEvent.id)
        if action_prefix:
            query = query.where(AuditEvent.action.startswith(action_prefix))
        return list(db.scalars(query))


def location_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalars(select(Location.id).where(Location.name == name)).one()


def user_id(email: str) -> int:
    with SessionLocal() as db:
        return db.scalars(select(User.id).where(User.email == email)).one()


def member(clients, email: str, role: str, locations: list[str] | None = None) -> TestClient:
    """A signed-in member of Corner Cafe; limited to some locations when given."""
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(OWNER["password"]),
            email_verified_at=datetime.now(UTC),
        )
        db.add(user)
        db.flush()
        membership = Membership(
            tenant_id=tenant.id, user_id=user.id, role=role, all_locations=locations is None
        )
        db.add(membership)
        db.flush()
        for name in locations or []:
            db.add(
                MembershipLocation(
                    tenant_id=tenant.id, membership_id=membership.id, location_id=location_id(name)
                )
            )
        db.commit()
    client = browser()
    clients.append(client)
    client.post("/login", data={"email": email, "password": OWNER["password"]})
    return client


def edit_location(client: TestClient, name: str, **changes):
    form = {"name": name, "timezone": "America/New_York", "currency": "USD"}
    form |= {"open_days": ["mon", "tue"], **changes}
    return client.post(f"/app/locations/{location_id(name)}", data=form)


# --- each audited change writes one event --------------------------------------------------------


def test_a_business_profile_change_is_recorded_with_what_it_was(corner):
    owner, _ = corner
    owner.post("/app/settings/business", data={"business_name": "Corner Coffee", "address": ""})
    (event,) = events()
    assert event.action == "settings.business_updated" and event.location_id is None
    assert event.before == {"name": "Corner Cafe"} and event.after == {"name": "Corner Coffee"}
    assert event.actor_user_id == user_id(OWNER["email"]) and event.actor_kind == "user"
    assert event.ip == "203.0.113.5"
    owner.post("/app/settings/business", data={"business_name": "Corner Coffee", "address": ""})
    assert len(events()) == 1  # saving without a change records nothing


def test_location_changes_are_recorded_against_the_location(corner):
    owner, _ = corner
    owner.post(
        "/app/locations/new",
        data={
            "name": "Pier",
            "timezone": "America/New_York",
            "currency": "USD",
            "open_days": ["sat", "sun"],
        },
    )
    pier = location_id("Pier")
    edit_location(owner, "Pier", business_day_cutoff_hour="4", open_days=["sat", "sun"])
    owner.post(f"/app/locations/{pier}/archive")
    owner.post(f"/app/locations/{pier}/restore")
    recorded = events("location.")
    assert [e.action for e in recorded] == [
        "location.created",
        "location.updated",
        "location.archived",
        "location.restored",
    ]
    assert {e.location_id for e in recorded} == {pier}
    assert recorded[1].before == {"business_day_cutoff_hour": 0}
    assert recorded[1].after == {"business_day_cutoff_hour": 4}
    assert "day start" in recorded[1].summary


def test_team_changes_are_recorded_by_who_made_them(corner, monkeypatch):
    owner, clients = corner
    sent = []
    monkeypatch.setattr("app.team.send_email", lambda *a, **k: sent.append(a) or True)
    owner.post("/app/team/invite", data={"email": "sam@example.test", "role": "manager"})
    token = sent[0][6].split("token=", 1)[1].split()[0]
    newcomer = browser("198.51.100.9")
    clients.append(newcomer)
    newcomer.post("/invitations/accept", data={"token": token, "password": "another-good-pw"})
    with SessionLocal() as db:
        sam = db.scalars(select(Membership).where(Membership.role == "manager")).one()
    owner.post(
        f"/app/team/{sam.id}/role",
        data={"role": "employee", "scope": "some", "location_ids": [location_id("Harbor")]},
    )
    owner.post("/app/team/invite", data={"email": "kim@example.test", "role": "read_only"})
    invitation_id = events("team.invited")[-1].subject_id
    owner.post(f"/app/team/invitations/{invitation_id}/withdraw")
    owner.post(f"/app/team/{sam.id}/remove")

    recorded = events("team.")
    assert [e.action for e in recorded] == [
        "team.invited",
        "team.joined",
        "team.role_changed",
        "team.invited",
        "team.invitation_withdrawn",
        "team.removed",
    ]
    joined, changed = recorded[1], recorded[2]
    assert joined.actor_user_id == user_id("sam@example.test") and joined.ip == "198.51.100.9"
    assert changed.before == {"role": "Manager", "locations": "all locations"}
    assert changed.after == {"role": "Employee", "locations": "Harbor"}
    assert all(e.actor_user_id == user_id(OWNER["email"]) for e in recorded if e is not joined)


def test_connecting_square_is_recorded(corner, monkeypatch):
    owner, _ = corner
    token = {
        "merchant_id": "M123",
        "access_token": "a",
        "refresh_token": "r",
        "expires_at": datetime.now(UTC) + timedelta(days=30),
    }
    monkeypatch.setattr("app.web.dashboard.oauth.exchange_code", lambda code: token)
    monkeypatch.setattr("app.web.dashboard.run_sync", lambda db, org: None)
    start = owner.get("/connect/square", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    owner.get("/connect/square/callback", params={"code": "c", "state": state})
    (event,) = events("connection.")
    assert event.summary == "Connected Square" and event.after["merchant"] == "M123"


def test_a_plan_change_from_stripe_is_recorded_as_the_product(db, tenant, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "stripe_price_pro", PRO_PRICE, raising=False)
    tenant.stripe_customer_id, tenant.stripe_subscription_id = "cus_1", "sub_1"
    db.commit()
    subscription = {
        "id": "sub_1",
        "customer": "cus_1",
        "status": "active",
        "items": {"data": [{"price": {"id": PRO_PRICE}}]},
    }
    event = {
        "id": "evt_1",
        "type": "customer.subscription.updated",
        "created": int(datetime.now(UTC).timestamp()),
        "data": {"object": subscription},
    }
    handle_event(db, event)
    db.commit()
    (recorded,) = events("billing.")
    assert recorded.actor_kind == "system" and recorded.actor_user_id is None
    assert recorded.after == {"plan": "pro", "status": "active"}
    handle_event(db, event | {"id": "evt_2"})
    db.commit()
    assert len(events("billing.")) == 1  # nothing changed the second time


# --- the history screen (AC-ADM-04) --------------------------------------------------------------


def test_settings_role_and_connection_changes_appear_in_the_history(corner, monkeypatch):
    owner, clients = corner
    owner.post("/app/settings/business", data={"business_name": "Corner Coffee", "address": ""})
    manager = member(clients, "lee@example.test", "manager")
    with SessionLocal() as db:
        lee_membership = db.scalars(select(Membership).where(Membership.role == "manager")).one()
    owner.post(f"/app/team/{lee_membership.id}/role", data={"role": "read_only"})
    token = {
        "merchant_id": "M1",
        "access_token": "a",
        "refresh_token": None,
        "expires_at": datetime.now(UTC) + timedelta(days=30),
    }
    monkeypatch.setattr("app.web.dashboard.oauth.exchange_code", lambda code: token)
    monkeypatch.setattr("app.web.dashboard.run_sync", lambda db, org: None)
    start = owner.get("/connect/square", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    owner.get("/connect/square/callback", params={"code": "c", "state": state})

    page = owner.get("/app/audit").text
    for words in ("Changed the business name", "from Manager", "Connected Square"):
        assert words in page
    assert "Corner Cafe &rarr; Corner Coffee" in page or "Corner Cafe → Corner Coffee" in page
    team_only = owner.get("/app/audit", params={"category": "team"}).text
    assert "from Manager" in team_only and "Connected Square" not in team_only
    by_owner = owner.get("/app/audit", params={"person": user_id(OWNER["email"])}).text
    assert "Connected Square" in by_owner
    assert manager.get("/app/audit").status_code == 403  # now read-only: no audit.view


def test_managers_see_their_locations_history_only(corner):
    owner, clients = corner
    owner.post("/app/settings/business", data={"business_name": "Corner Coffee", "address": ""})
    edit_location(owner, "Main St", business_day_cutoff_hour="5")
    edit_location(owner, "Harbor", business_day_cutoff_hour="6")
    harbor_only = member(clients, "hana@example.test", "manager", locations=["Harbor"])
    everywhere = member(clients, "max@example.test", "manager")

    limited = harbor_only.get("/app/audit").text
    assert "for Harbor" in limited and "for Main St" not in limited
    assert "Changed the business" not in limited
    wide = everywhere.get("/app/audit").text
    assert "for Harbor" in wide and "for Main St" in wide
    assert "Changed the business" not in wide  # business-wide history is for Owners and Admins
    employee = member(clients, "eve@example.test", "employee")
    assert employee.get("/app/audit").status_code == 403


def test_the_history_pages_back_through_older_changes(corner, monkeypatch):
    owner, _ = corner
    monkeypatch.setattr("app.audit.PAGE", 2)
    monkeypatch.setattr("app.web.audit.PAGE", 2)
    for n in range(3):
        owner.post("/app/settings/business", data={"business_name": f"Corner {n}", "address": ""})
    first = owner.get("/app/audit").text
    assert "Corner 2" in first and "Corner 0 &rarr; Corner 1" in first and "Older changes" in first
    older_link = first.split('href="/app/audit?', 1)[1].split('"', 1)[0].replace("&amp;", "&")
    older = owner.get("/app/audit?" + older_link).text
    assert "Corner Cafe &rarr; Corner 0" in older and "Corner 2" not in older


# --- append-only ---------------------------------------------------------------------------------


def test_audit_events_cannot_be_updated_through_the_scoped_layer():
    with pytest.raises(UnscopedAccess, match="append-only"):
        update_scoped(OrgContext.system(1), AuditEvent)


def test_only_the_audit_module_writes_audit_events():
    """No other code can edit or delete history: AuditEvent is used nowhere else."""
    allowed = {"app/models.py", "app/audit.py", "app/access/scoped.py"}
    users = set()
    for path in Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        if "AuditEvent" in names:
            users.add(path.as_posix())
    assert users <= allowed, users - allowed
