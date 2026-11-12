"""T1.19: exceptions (REC-FR-11..16, AC-REC-06..09, WF-27, DM-8, ADR-0043)."""

import ast
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.db import SessionLocal, is_postgres
from app.exceptions import NotFound, acknowledge, dismiss, listing, record_exception
from app.ledger.posting import balance, post_transaction
from app.main import app
from app.models import (
    AuditEvent,
    ExceptionRecord,
    Location,
    Membership,
    MembershipLocation,
    StockItem,
    TrackMode,
    User,
)
from app.orchestration.alerts import evaluate_and_store
from app.security import hash_password
from app.transform.alerts import AlertProposal
from app.transform.exceptions import (
    STOCK_TYPES,
    ExceptionRuleError,
    Finding,
    check_dismissal,
    check_finding,
    from_alert,
    reconcile,
    visible_to,
)
from app.transform.ledger import Line
from app.units import Dimension
from tests.ledger_helpers import opened

D = Decimal
PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)
postgres_only = pytest.mark.skipif(
    not is_postgres(),
    reason="two sessions locking rows at once is a Postgres property (ADR-0018)",
)


def finding(kind="unmapped_product", subject="VAR-1", **extra) -> Finding:
    defaults = {"subject_type": "external_product", "title": f"{subject} is not mapped"}
    return Finding(type=kind, subject_id=subject, **{**defaults, **extra})


def stock(db, location, slug="milk", qty="10", reorder="20"):
    item = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=slug,
        name=slug.title(),
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=D(qty),
        reorder_point_base=D(reorder),
        cost_per_base_cents=D("50"),
    )
    db.add(item)
    opened(db, item)
    db.commit()
    return item


def member(db, tenant, role: str, locations: list[int] | None = None) -> OrgContext:
    user = User(
        tenant_id=tenant.id, email=f"{role}@rosewood.test", password_hash=hash_password(PASSWORD)
    )
    db.add(user)
    db.flush()
    membership = Membership(
        tenant_id=tenant.id, user_id=user.id, role=role, all_locations=locations is None
    )
    db.add(membership)
    db.flush()
    for location_id in locations or []:
        db.add(
            MembershipLocation(
                tenant_id=tenant.id, membership_id=membership.id, location_id=location_id
            )
        )
    db.commit()
    return OrgContext.for_membership(membership)


def signed_in(email: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": email, "password": PASSWORD})
    return client


# --- the rules (pure) --------------------------------------------------------------------------


def test_a_run_opens_what_is_new_refreshes_what_is_there_and_clears_only_its_own_types():
    held = [("low_stock", "stock_item", "1"), ("low_stock", "stock_item", "2")]
    held.append(("unmapped_product", "external_product", "VAR-9"))
    found = [
        Finding("low_stock", "stock_item", "1", "Milk is low"),
        Finding("out_of_stock", "stock_item", "3", "Oats are out"),
    ]
    plan = reconcile(held, found, STOCK_TYPES)
    assert [f.key for f in plan.opened] == [("out_of_stock", "stock_item", "3")]
    assert [f.key for f in plan.refreshed] == [("low_stock", "stock_item", "1")]
    # Item 2 recovered; the unmapped product is not this run's to clear.
    assert plan.cleared == [("low_stock", "stock_item", "2")]


def test_a_finding_must_name_a_known_type_a_subject_and_a_title():
    for bad in (
        finding(kind="made_up"),
        finding(subject=" "),
        finding(title=""),
        finding(severity="urgent"),
    ):
        with pytest.raises(ExceptionRuleError):
            check_finding(bad)


def test_a_dismissal_needs_a_reason():
    for empty in (None, "", "   "):
        with pytest.raises(ExceptionRuleError):
            check_dismissal(empty)
    assert check_dismissal("  Counted   it myself ") == "Counted it myself"


def test_money_problems_are_hidden_from_roles_without_cost_access():
    assert visible_to("missing_cost", can_view_costs=True)
    assert not visible_to("missing_cost", can_view_costs=False)
    assert not visible_to("price_variance", can_view_costs=False)
    assert visible_to("waste_risk", can_view_costs=False)  # operational; its amount is withheld


def test_the_five_alert_kinds_map_onto_exception_types():
    proposal = AlertProposal(7, "expired", "Milk passed its date", D(8), D(0), None, D(0), 1, 560)
    mapped = from_alert(proposal, location_id=3)
    assert mapped.key == ("batch_expired_unresolved", "stock_item", "7")
    assert (mapped.location_id, mapped.money_impact_cents) == (3, 560)


# --- de-duplication (REC-FR-13, AC-REC-06) -----------------------------------------------------


def test_ac_rec_06_ten_occurrences_of_one_problem_are_one_open_exception(db, tenant):
    org = OrgContext.system(tenant.id)
    for _sale in range(10):
        record_exception(db, org, finding())
    record_exception(db, org, finding(subject="VAR-2"))
    db.commit()
    rows = db.scalars(select(ExceptionRecord).order_by(ExceptionRecord.id)).all()
    assert [(r.subject_id, r.occurrences, r.status) for r in rows] == [
        ("VAR-1", 10, "open"),
        ("VAR-2", 1, "open"),
    ]


def test_the_database_allows_one_open_exception_per_type_and_subject(db, tenant):
    org = OrgContext.system(tenant.id)
    first = record_exception(db, org, finding())
    db.commit()
    db.add(
        ExceptionRecord(
            tenant_id=tenant.id,
            type="unmapped_product",
            severity="warning",
            subject_type="external_product",
            subject_id="VAR-1",
            title="again",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    dismiss(db, org, first.id, "Not ours")
    db.commit()
    again = record_exception(db, org, finding())  # once closed, the next one is a new exception
    db.commit()
    assert again.id != first.id and again.occurrences == 1


@postgres_only
def test_two_runs_reporting_the_same_problem_at_once_share_one_exception(db, tenant):
    org = OrgContext.system(tenant.id)
    for trial in range(10):
        start = threading.Barrier(2)
        errors: list[Exception] = []

        def report(subject=f"VAR-{trial}", start=start, errors=errors):
            try:
                with SessionLocal() as session:
                    start.wait(timeout=10)
                    record_exception(session, org, finding(subject=subject))
                    session.commit()
            except Exception as exc:  # noqa: BLE001 (reported by the assertion below)
                errors.append(exc)

        threads = [threading.Thread(target=report) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not errors, errors
        with SessionLocal() as check:
            rows = check.scalars(
                select(ExceptionRecord).where(ExceptionRecord.subject_id == f"VAR-{trial}")
            ).all()
            assert [r.occurrences for r in rows] == [2]


# --- auto-resolve (REC-FR-14, AC-REC-07) -------------------------------------------------------


def test_ac_rec_07_an_exception_resolves_itself_when_its_condition_clears(db, tenant, location):
    org = OrgContext.system(tenant.id)
    milk = stock(db, location)
    record_exception(db, org, finding())  # not a stock type: the evaluation leaves it alone
    evaluate_and_store(db, org)
    db.commit()
    low = db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "low_stock")).one()
    assert (low.status, low.subject_id, low.location_id) == ("open", str(milk.id), location.id)

    evaluate_and_store(db, org)  # still low: refreshed, not opened again
    post_transaction(
        db,
        org,
        kind="receipt",
        source_type="test",
        source_id="1",
        idempotency_key="delivery",
        lines=[Line(location.id, milk.inventory_item_id, "receipt", D(40))],
        occurred_at=datetime.now(UTC),
    )
    stats = evaluate_and_store(db, org)
    db.commit()
    db.expire_all()
    low = db.get(ExceptionRecord, low.id)
    assert stats["resolved"] == 1
    assert (low.status, low.resolution, low.resolved_by_user_id) == ("resolved", "system", None)
    assert low.resolution_note == "The condition cleared"
    assert db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "low_stock")).all() == [
        low
    ]
    unmapped = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "unmapped_product")
    ).one()
    assert unmapped.status == "open"


# --- people acting on them (AC-REC-08) ---------------------------------------------------------


def test_ac_rec_08_dismissing_needs_a_reason_and_never_touches_stock(db, tenant, location):
    milk = stock(db, location)
    manager = member(db, tenant, "manager")
    evaluate_and_store(db, OrgContext.system(tenant.id))
    db.commit()
    low = db.scalars(select(ExceptionRecord)).one()
    before = balance(db, manager, location.id, milk.inventory_item_id)

    with pytest.raises(ExceptionRuleError):
        dismiss(db, manager, low.id, "  ")
    dismiss(db, manager, low.id, "Supplier delivers tomorrow")
    db.commit()

    db.expire_all()
    low = db.get(ExceptionRecord, low.id)
    assert (low.status, low.resolution, low.resolution_note) == (
        "dismissed",
        "dismissed",
        "Supplier delivers tomorrow",
    )
    assert low.resolved_by_user_id == manager.user_id
    assert balance(db, manager, location.id, milk.inventory_item_id) == before
    event = db.scalars(select(AuditEvent)).one()
    assert (event.action, event.after) == (
        "exception.dismissed",
        {"reason": "Supplier delivers tomorrow"},
    )


def test_the_database_refuses_a_dismissal_without_a_reason(db, tenant):
    db.add(
        ExceptionRecord(
            tenant_id=tenant.id,
            type="low_stock",
            severity="warning",
            subject_type="stock_item",
            subject_id="1",
            title="Milk is low",
            status="dismissed",
            resolution="dismissed",
            resolved_at=datetime.now(UTC),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()


def test_acknowledging_keeps_it_open_and_happens_once(db, tenant):
    manager = member(db, tenant, "manager")
    row = record_exception(db, OrgContext.system(tenant.id), finding())
    db.commit()
    acknowledge(db, manager, row.id)
    db.commit()
    with pytest.raises(ExceptionRuleError):
        acknowledge(db, manager, row.id)
    again = record_exception(db, OrgContext.system(tenant.id), finding())
    db.commit()
    assert (again.id, again.status, again.occurrences) == (row.id, "acknowledged", 2)
    assert again.acknowledged_by_user_id == manager.user_id


def test_the_screen_acknowledges_and_dismisses_and_refuses_a_bare_dismissal(db, tenant):
    member(db, tenant, "manager")
    row = record_exception(db, OrgContext.system(tenant.id), finding())
    db.commit()
    client = signed_in("manager@rosewood.test")
    page = client.get("/app/exceptions")
    assert page.status_code == 200 and "VAR-1 is not mapped" in page.text

    client.post(f"/app/exceptions/{row.id}/acknowledge")
    refused = client.post(f"/app/exceptions/{row.id}/dismiss", data={"reason": " "})
    assert "Say why it is being dismissed." in refused.text
    done = client.post(f"/app/exceptions/{row.id}/dismiss", data={"reason": "Test product"})
    assert "Dismissed." in done.text
    assert "VAR-1 is not mapped" not in done.text  # off the open list
    closed = client.get("/app/exceptions", params={"status": "closed"}).text
    assert "Dismissed: Test product" in closed


# --- who sees what (REC-FR-16, AC-REC-09) ------------------------------------------------------


def test_ac_rec_09_employees_see_operational_exceptions_without_amounts(db, tenant, location):
    org = OrgContext.system(tenant.id)
    record_exception(
        db, org, finding("waste_risk", "1", subject_type="stock_item", money_impact_cents=2640)
    )
    record_exception(db, org, finding("missing_cost", "2", subject_type="stock_item"))
    db.commit()
    employee = member(db, tenant, "employee")
    owner = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))

    theirs = listing(db, employee)
    assert [(v.type, v.money_impact_cents) for v in theirs] == [("waste_risk", None)]
    assert {(v.type, v.money_impact_cents) for v in listing(db, owner)} == {
        ("waste_risk", 2640),
        ("missing_cost", None),
    }
    hidden = db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "missing_cost")).one()
    with pytest.raises(NotFound):
        dismiss(db, employee, hidden.id, "not mine")

    page = signed_in("employee@rosewood.test").get("/app/exceptions").text
    assert "May expire unsold" in page and "$26.40" not in page and "Cost missing" not in page


def test_an_employee_cannot_acknowledge_or_dismiss(db, tenant):
    member(db, tenant, "employee")
    row = record_exception(db, OrgContext.system(tenant.id), finding())
    db.commit()
    client = signed_in("employee@rosewood.test")
    assert client.post(f"/app/exceptions/{row.id}/acknowledge").status_code == 403
    assert client.post(f"/app/exceptions/{row.id}/dismiss", data={"reason": "x"}).status_code == 403
    assert "Acknowledge" not in client.get("/app/exceptions").text


def test_a_location_limited_member_sees_only_their_locations(db, tenant, location):
    harbor = Location(tenant_id=tenant.id, name="Harbor", timezone="UTC")
    db.add(harbor)
    db.flush()
    stock(db, location, "milk")
    stock(db, harbor, "oats")
    evaluate_and_store(db, OrgContext.system(tenant.id))
    db.commit()
    there = member(db, tenant, "manager", locations=[harbor.id])
    assert [v.subject_name for v in listing(db, there)] == ["Oats"]


# --- nothing else writes them (static) ---------------------------------------------------------


def test_only_the_exceptions_module_creates_exceptions_and_nothing_writes_alerts():
    found = []
    for path in sorted(Path("app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name == "Alert" or (
                name == "ExceptionRecord" and path.as_posix() != "app/exceptions.py"
            ):
                found.append(f"{path.as_posix()}:{node.lineno} {name}(")
    assert not found, found
