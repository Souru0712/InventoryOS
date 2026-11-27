"""T4.08: the plan and entitlements view (ADM-FR-09, ADM-FR-15): the values match the entitlement
set, usage is counted the way the limits are, and only Owners and Administrators see it."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.access import OrgContext
from app.db import SessionLocal
from app.jobs import enqueue
from app.models import Tenant
from app.plans import plan_by_code
from tests.test_audit import corner, member  # noqa: F401  (the fixture, by name)


def _tenant(db) -> Tenant:
    return db.scalars(select(Tenant)).one()


def test_the_page_shows_the_plan_its_limits_and_usage(corner):  # noqa: F811
    owner, _clients = corner
    with SessionLocal() as db:
        tenant = _tenant(db)
        org = OrgContext.system(tenant.id)
        for n in range(3):
            enqueue(db, org, "extract_document", {"document_id": n}, dedupe_key=f"d{n}")
        db.commit()

    page = owner.get("/app/subscription")
    assert page.status_code == 200
    text = page.text
    assert "Vontry Complete" in text and "Billed per location" in text
    assert "sales from a POS or sales entered by hand or imported" in text
    assert "as many as you have" in text
    assert "Active locations</td><td><strong>2</strong>" in text  # Main St and Harbor
    assert "Invoices read in the last 24 hours</td><td><strong>3</strong> of 40" in text
    assert "On subscribing, billed for <strong>1</strong> location" in text, "none on a POS yet"


def test_standalone_shows_its_one_location_and_its_price(corner):  # noqa: F811
    owner, _clients = corner
    with SessionLocal() as db:
        standalone = plan_by_code(db, "standalone")
        standalone.display_amount_cents, standalone.display_currency = 9900, "USD"
        standalone.display_interval = "month"
        _tenant(db).billing_plan = standalone
        db.commit()

    text = owner.get("/app/subscription").text
    assert "Vontry Standalone" in text and "$99.00 for the business a month" in text
    assert "Active locations</td><td><strong>2</strong> of 1" in text, "over the limit, shown"
    assert "One subscription for the business." in text


def test_a_subscribed_business_sees_what_stripe_bills(corner):  # noqa: F811
    owner, _clients = corner
    with SessionLocal() as db:
        tenant = _tenant(db)
        tenant.stripe_subscription_id, tenant.billed_quantity = "sub_1", 2
        tenant.current_period_end = datetime(2026, 12, 27, tzinfo=UTC)
        db.commit()
    text = owner.get("/app/subscription").text
    assert "Billed for <strong>2</strong> locations taking sales from a POS" in text


def test_owners_and_administrators_see_it_and_nobody_else(corner):  # noqa: F811
    owner, clients = corner
    administrator = member(clients, "ada@corner.test", "administrator")
    manager = member(clients, "max@corner.test", "manager")
    employee = member(clients, "eve@corner.test", "employee")

    assert administrator.get("/app/subscription").status_code == 200
    assert (
        "The account owner manages the subscription." in administrator.get("/app/subscription").text
    )
    assert manager.get("/app/subscription").status_code == 403
    assert employee.get("/app/subscription").status_code == 403
    assert "What it includes" in owner.get("/app/settings").text
