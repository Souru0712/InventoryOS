"""T4.06: account deletion and retention (ADM-FR-12, SAAS-D-03..05, C-44, AC-ADM-05).

An Owner's request can be cancelled for 7 days, then removes the business's rows and files,
revokes its POS connection and cancels its subscription; a business read-only for 90 days is
scheduled the same way, and subscribing again keeps it.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.db import Base, SessionLocal
from app.deletion import (
    TOMBSTONE,
    DeletionRefused,
    cancel_deletion,
    request_deletion,
    run_deletion,
    sweep_retention,
)
from app.models import (
    AuditEvent,
    Document,
    EmailLog,
    Job,
    Location,
    Membership,
    SubscriptionStatus,
    Tenant,
    User,
)
from app.orchestration.subscriptions import start_subscription
from app.security import hash_password
from app.storage import StorageError, get_storage
from tests.test_audit import corner, member  # noqa: F401  (the fixture, by name)
from tests.test_isolation import A_CODE, _business


@pytest.fixture
def shop(db, tenant):
    """Rosewood with one of everything; its Owner's password is "a-good-password"."""
    ids = _business(db, tenant.id, "Rosewood", A_CODE)
    owner = db.scalars(select(User).where(User.tenant_id == tenant.id)).one()
    db.commit()
    org = OrgContext(tenant_id=tenant.id, user_id=owner.id, permissions=role_permissions("owner"))
    return org, owner, ids


@pytest.fixture
def spies(monkeypatch):
    calls = {"disconnect": 0, "cancelled": [], "sent": []}

    def disconnect(db, org, *args, **kwargs):
        calls["disconnect"] += 1
        return True

    class Subscriptions:
        @staticmethod
        def cancel(subscription_id):
            calls["cancelled"].append(subscription_id)

    monkeypatch.setattr("app.deletion.disconnect", disconnect)
    monkeypatch.setattr(
        "app.billing.stripe_client", lambda: type("S", (), {"Subscription": Subscriptions})
    )
    real_send = __import__("app.deletion", fromlist=["send_email"]).send_email

    def send(db, tenant_id, to, subject, html, kind, text):
        calls["sent"].append((to, kind))
        return real_send(db, tenant_id, to, subject, html, kind, text)

    monkeypatch.setattr("app.deletion.send_email", send)
    return calls


def _owned_rows(db, tenant_id: int) -> dict[str, int]:
    counts = {}
    for table in Base.metadata.sorted_tables:
        if table.name in ("tenants", "users") or "tenant_id" not in table.c:
            continue
        found = db.scalar(
            select(func.count()).select_from(table).where(table.c.tenant_id == tenant_id)
        )
        if found:
            counts[table.name] = found
    return counts


# --- asking and cancelling ------------------------------------------------------------------------


def test_asking_needs_the_owners_password(db, shop, spies):
    org, owner, _ids = shop
    with pytest.raises(DeletionRefused, match="password is not right"):
        request_deletion(db, org, owner, "wrong")
    tenant = request_deletion(db, org, owner, "a-good-password")
    db.commit()

    assert tenant.deletion_reason == "owner" and tenant.deletion_requested_by == owner.id
    assert tenant.deletion_due_at - tenant.deletion_requested_at == timedelta(days=7)
    job = db.scalars(select(Job).where(Job.kind == "delete_business")).one()
    assert job.run_at == tenant.deletion_due_at, "it runs when the window closes"
    assert (owner.email, "deletion_scheduled") in spies["sent"], "every Owner is told"
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.action == "data.deletion_requested")
    ).one()
    assert event.actor_user_id == owner.id


def test_a_cancelled_deletion_deletes_nothing(db, shop, spies):
    org, owner, _ids = shop
    request_deletion(db, org, owner, "a-good-password")
    assert cancel_deletion(db, org) is True
    db.commit()
    before = _owned_rows(db, org.tenant_id)

    assert run_deletion(db, org, datetime.now(UTC) + timedelta(days=8)) is False
    assert _owned_rows(db, org.tenant_id) == before


def test_nothing_happens_before_the_window_closes(db, shop, spies):
    org, owner, _ids = shop
    request_deletion(db, org, owner, "a-good-password")
    assert run_deletion(db, org, datetime.now(UTC) + timedelta(days=6)) is False
    assert spies["disconnect"] == 0


# --- when it falls due (AC-ADM-05) ----------------------------------------------------------------


def test_a_due_deletion_removes_every_row_and_file(db, shop, spies):
    org, owner, ids = shop
    tenant = db.get(Tenant, org.tenant_id)
    tenant.stripe_subscription_id = "sub_1"
    document = db.get(Document, ids["document"])
    key = document.storage_key
    request_deletion(db, org, owner, "a-good-password")
    db.commit()
    assert _owned_rows(db, org.tenant_id), "the business has rows to delete"

    assert run_deletion(db, org, datetime.now(UTC) + timedelta(days=7, seconds=1)) is True
    db.commit()

    assert _owned_rows(db, org.tenant_id) == {}, "no row of the business is left"
    with pytest.raises(StorageError):
        get_storage().get(key)
    assert spies["disconnect"] == 1, "the POS connection is revoked"
    assert spies["cancelled"] == ["sub_1"], "billing stops"
    tenant = db.get(Tenant, org.tenant_id)
    assert (tenant.name, tenant.stripe_subscription_id) == (TOMBSTONE, None)
    assert tenant.deleted_at is not None
    assert db.get(User, owner.id) is None, "a person whose only business it was goes with it"
    assert (owner.email, "deletion_done") in spies["sent"], "written confirmation"
    assert db.scalars(select(EmailLog).where(EmailLog.tenant_id == org.tenant_id)).first() is None


def test_someone_in_another_business_keeps_their_account(db, shop, spies):
    org, owner, _ids = shop
    other = Tenant(name="Juniper")
    db.add(other)
    db.flush()
    shared = User(
        tenant_id=org.tenant_id, email="sam@example.test", password_hash=hash_password("x" * 12)
    )
    db.add(shared)
    db.flush()
    db.add_all(
        [
            Membership(tenant_id=org.tenant_id, user_id=shared.id, role="manager"),
            Membership(tenant_id=other.id, user_id=shared.id, role="owner"),
        ]
    )
    db.add(Location(tenant_id=other.id, name="Juniper Main", timezone="UTC"))
    request_deletion(db, org, owner, "a-good-password")
    db.commit()

    run_deletion(db, org, datetime.now(UTC) + timedelta(days=8))
    db.commit()
    assert db.get(User, shared.id) is not None
    assert [
        m.tenant_id for m in db.scalars(select(Membership).where(Membership.user_id == shared.id))
    ] == [other.id]
    assert (
        db.scalars(select(Location).where(Location.tenant_id == other.id)).one().name
        == "Juniper Main"
    )


def test_a_repeat_of_the_job_does_nothing(db, shop, spies):
    org, owner, _ids = shop
    request_deletion(db, org, owner, "a-good-password")
    db.commit()
    later = datetime.now(UTC) + timedelta(days=8)
    assert run_deletion(db, org, later) is True
    db.commit()
    assert run_deletion(db, org, later) is False


# --- retention (SAAS-D-03) ------------------------------------------------------------------------


def _lapsed(db, tenant, days: int) -> None:
    tenant.subscription_status = SubscriptionStatus.CANCELED
    tenant.current_period_end = datetime.now(UTC) - timedelta(days=days)
    db.commit()


def test_retention_schedules_the_deletion_after_90_days_read_only(db, shop, spies):
    org, owner, _ids = shop
    tenant = db.get(Tenant, org.tenant_id)
    _lapsed(db, tenant, 80)
    assert sweep_retention(db) == []
    _lapsed(db, tenant, 91)
    assert sweep_retention(db) == [tenant.id]
    db.commit()
    assert tenant.deletion_reason == "retention" and tenant.deletion_requested_by is None
    assert (owner.email, "deletion_scheduled") in spies["sent"]
    assert sweep_retention(db) == [], "scheduled once"


def test_subscribing_again_keeps_the_business(db, shop, spies):
    org, _owner, _ids = shop
    tenant = db.get(Tenant, org.tenant_id)
    _lapsed(db, tenant, 91)
    sweep_retention(db)
    start_subscription(db, tenant, "complete")
    db.commit()
    assert tenant.deletion_due_at is None
    assert run_deletion(db, org, datetime.now(UTC) + timedelta(days=8)) is False
    assert spies["disconnect"] == 0


# --- the screens ----------------------------------------------------------------------------------


def test_an_owner_asks_and_keeps_it_on_the_page(corner):  # noqa: F811
    owner, _clients = corner
    page = owner.get("/app/settings/deletion")
    assert page.status_code == 200 and "Delete Corner Cafe" in page.text

    wrong = owner.post("/app/settings/deletion", data={"password": "nope"})
    assert wrong.status_code == 400 and "password is not right" in wrong.text

    asked = owner.post(
        "/app/settings/deletion", data={"password": "a-good-password"}, follow_redirects=False
    )
    assert asked.status_code == 303
    assert "will be deleted on" in owner.get("/app").text, "a banner while it is scheduled"

    owner.post("/app/settings/deletion/cancel", follow_redirects=False)
    assert "will be deleted on" not in owner.get("/app").text
    with SessionLocal() as db:
        assert db.scalars(select(Tenant)).one().deletion_due_at is None


def test_only_owners_may_delete_and_read_only_does_not_stop_them(corner):  # noqa: F811
    owner, clients = corner
    administrator = member(clients, "ada@corner.test", "administrator")
    assert administrator.get("/app/settings/deletion").status_code == 403
    with SessionLocal() as db:
        db.scalars(select(Tenant)).one().subscription_status = SubscriptionStatus.CANCELED
        db.commit()
    asked = owner.post(
        "/app/settings/deletion", data={"password": "a-good-password"}, follow_redirects=False
    )
    assert asked.status_code == 303
