"""T1.06: every operation checks a permission key; roles hold the keys the matrix gives them."""

import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.access.permissions import (
    ALL_PERMISSIONS,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    VIEW_COSTS,
    known,
    role_permissions,
    without_costs,
)
from app.db import SessionLocal
from app.main import app
from app.models import (
    DocumentLine,
    Location,
    Membership,
    MembershipLocation,
    StockItem,
    Tenant,
    User,
)
from app.security import hash_password
from app.units import Dimension
from tests.test_async_correctness import api_routes
from tests.test_isolation import CASES, PATH_ROWS, _business

# Routes anyone may call: the public site, signing in and out, and signed provider webhooks.
PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/"),
    ("GET", "/pricing"),
    ("GET", "/terms"),
    ("GET", "/privacy"),
    ("GET", "/signup"),
    ("POST", "/signup"),
    ("GET", "/login"),
    ("POST", "/login"),
    ("POST", "/logout"),
    ("POST", "/webhooks/square"),
    ("POST", "/billing/webhook"),
    ("GET", "/verify-email"),
    ("POST", "/verify-email"),
    ("GET", "/password/forgot"),
    ("POST", "/password/forgot"),
    ("GET", "/password/reset"),
    ("POST", "/password/reset"),
}
# Routes about the signed-in person rather than the business: any active member.
PERSONAL = {
    ("GET", "/app/settings"),
    ("POST", "/app/settings/email"),
    ("POST", "/app/organizations/switch"),
    ("POST", "/app/sessions/end-all"),
    ("POST", "/app/verify-email/resend"),
    ("POST", "/app/settings/password"),
}
ROLES = ("owner", "administrator", "manager", "employee", "read_only")


def declared_permission(route) -> str | None:
    found = []

    def walk(dependant):
        permission = getattr(dependant.call, "permission", None)
        if permission:
            found.append(permission)
        for sub in dependant.dependencies:
            walk(sub)

    walk(route.dependant)
    return found[0] if found else None


def declared_routes() -> dict[tuple[str, str], str]:
    return {
        (method, route.path): key
        for route in api_routes(app.routes)
        if (key := declared_permission(route))
        for method in route.methods
    }


# --- the catalogue ------------------------------------------------------------------------------


def test_the_owner_holds_every_key_and_the_presets_follow_the_matrix():
    assert ROLE_PERMISSIONS["owner"] == ALL_PERMISSIONS
    assert set(ROLE_PERMISSIONS) == set(ROLES)
    employee, manager = ROLE_PERMISSIONS["employee"], ROLE_PERMISSIONS["manager"]
    administrator, read_only = ROLE_PERMISSIONS["administrator"], ROLE_PERMISSIONS["read_only"]
    assert VIEW_COSTS not in employee  # CMD-FR-14
    assert {"receiving.post", "count.record", "waste.record"} <= employee  # PD-19
    assert "sales.record" not in employee  # PD-37: Manager+ by default
    assert {"production.void", "waste.void"}.isdisjoint(employee)  # the undo window comes later
    assert {"pos.manage", "location.manage", "team.manage"}.isdisjoint(manager)
    assert {"owner.manage", "subscription.manage", "account.delete"}.isdisjoint(administrator)
    assert read_only == {
        "inventory.view",
        VIEW_COSTS,
        "recipe.view",
        "reorder.view",
        "exception.view",
        "reports.operational",
        "reports.financial",
    }
    assert employee < manager < administrator < ROLE_PERMISSIONS["owner"]


def test_an_unknown_key_is_a_programming_error_and_an_unknown_role_holds_nothing():
    with pytest.raises(KeyError):
        known("inventory.veiw")
    assert role_permissions("superuser") == frozenset()


def test_cost_fields_are_removed_at_any_depth():
    payload = {"item": {"name": "Milk", "cost_cents": 95, "lots": [{"value_cents": 1}]}}
    assert without_costs(payload) == {"item": {"name": "Milk", "lots": [{}]}}


# --- every route declares what it needs ---------------------------------------------------------


def test_every_route_declares_a_permission_or_is_public_or_personal():
    """Deny by default: a new route fails here until it says which key it needs."""
    routes = {(method, route.path) for route in api_routes(app.routes) for method in route.methods}
    declared = declared_routes()
    undeclared = routes - set(declared) - PUBLIC - PERSONAL
    assert undeclared == set(), f"declare require_permission(...) on {sorted(undeclared)}"
    assert set(declared.values()) <= set(PERMISSIONS)
    assert (PUBLIC | PERSONAL) & set(declared) == set(), "a listed route also declares a key"
    assert len(declared) >= 45


# --- AC-STF-02: a role without the key gets 403 -------------------------------------------------


@pytest.fixture
def client_factory():
    """A business with one of everything, and a signed-in client for any role in it."""
    from app.db import Base, engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        tenant = Tenant(name="Rosewood")
        db.add(tenant)
        db.flush()
        ids = _business(db, tenant.id, "Rosewood", "110000000014")
        people = {}
        for role in ROLES:
            user = User(
                tenant_id=tenant.id,
                email=f"{role}@rosewood.test",
                password_hash=hash_password("a-good-password"),
                email_verified_at=datetime.now(UTC),
            )
            db.add(user)
            db.flush()
            membership = Membership(tenant_id=tenant.id, user_id=user.id, role=role)
            db.add(membership)
            db.flush()
            people[role] = membership.id
        db.commit()
        ids["membership"] = people["owner"]

    clients = []

    def sign_in(role: str) -> TestClient:
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.post(
            "/login", data={"email": f"{role}@rosewood.test", "password": "a-good-password"}
        )
        return client

    yield sign_in, ids, people
    for client in clients:
        client.__exit__(None, None, None)


@pytest.mark.parametrize("role", ROLES)
def test_a_role_without_the_key_is_refused_and_a_role_with_it_is_not(client_factory, role):
    sign_in, ids, people = client_factory
    client = sign_in(role)
    ids = ids | {"membership": people[role]}
    held = role_permissions(role)
    checked = 0
    for (method, path), key in sorted(declared_routes().items()):
        case = CASES.get((method, path), lambda _ids: {})
        url = path.format(**{name: ids[row] for name, row in PATH_ROWS.items()})
        response = client.request(method, url, follow_redirects=False, **case(ids))
        if key in held:
            assert response.status_code not in (403, 500), (role, method, path, response.text[:200])
        else:
            assert response.status_code == 403, (role, method, path, response.status_code)
        checked += 1
    assert checked >= 45


# --- AC-CMD-03 and §5: no costs for a role without them -----------------------------------------

MONEY = re.compile(r"\$\s?\d")


def test_an_employee_dashboard_carries_no_cost_or_value(client_factory):
    sign_in, ids, _people = client_factory
    owner, employee = sign_in("owner"), sign_in("employee")
    assert MONEY.search(owner.get("/app").text), "the owner's dashboard shows money"
    for page in ("/app", "/app/expiring", f"/app/receiving/{ids['document']}", "/app/receiving"):
        response = employee.get(page)
        assert response.status_code == 200, page
        assert not MONEY.search(response.text), page
    lookup = employee.get("/app/scan/lookup", params={"code": "110000000014"}).json()
    assert lookup["found"] and "cost_cents" not in lookup["item"]
    assert (
        "cost_cents"
        in owner.get("/app/scan/lookup", params={"code": "110000000014"}).json()["item"]
    )


def test_the_menu_shows_what_the_role_may_open(client_factory):
    sign_in, _ids, _people = client_factory
    employee = sign_in("employee").get("/app").text
    owner = sign_in("owner").get("/app").text
    for link in ('href="/app/promotions"', 'href="/app/report"'):
        assert link in owner and link not in employee
    assert 'href="/app/receiving"' in employee  # PD-19


def test_an_employee_cannot_change_what_a_line_cost(client_factory):
    sign_in, ids, _people = client_factory
    line = ids["line"]
    with SessionLocal() as db:
        before = db.get(DocumentLine, line).line_total_cents
    sign_in("employee").post(
        f"/app/receiving/{ids['document']}/apply",
        data={
            f"line_{line}_action": "apply",
            f"line_{line}_item": ids["item"],
            f"line_{line}_qty": "2",
            f"line_{line}_total": "999.00",
        },
    )
    with SessionLocal() as db:
        assert db.get(DocumentLine, line).line_total_cents == before


# --- AC-STF-08: a member scoped to one location -------------------------------------------------


def test_a_manager_of_one_location_sees_and_changes_only_that_location(client_factory):
    sign_in, ids, people = client_factory
    with SessionLocal() as db:
        tenant_id = ids["tenant"]
        other = Location(
            tenant_id=tenant_id, provider=None, provider_location_id=None, name="Harbour"
        )
        db.add(other)
        db.flush()
        harbour_item = StockItem(
            tenant_id=tenant_id,
            location_id=other.id,
            slug="harbour-oats",
            name="Harbour oats",
            dimension=Dimension.COUNT,
            stock_unit="each",
            qty_base=Decimal("4"),
            reorder_point_base=Decimal("10"),
        )
        db.add(harbour_item)
        manager = db.get(Membership, people["manager"])
        manager.all_locations = False
        manager.locations.append(
            MembershipLocation(tenant_id=tenant_id, location_id=ids["location"])
        )
        db.commit()
        harbour, oats = other.id, harbour_item.id

    client = sign_in("manager")
    # Every page still works for a location-limited member.
    for page in ("/app", "/app/expiring", "/app/receiving", "/app/reorder", "/app/health"):
        response = client.get(page)
        assert response.status_code == 200, page
        assert "Harbour oats" not in response.text, page
    assert "Rosewood protein bars" in client.get("/app").text
    # The other location's rows, reports and exports are not found.
    assert client.post(f"/app/items/{oats}/count", data={"quantity": "1"}).status_code == 404
    assert client.get(f"/app/scan/codes/{oats}").status_code == 404
    assert client.get("/app/reorder", params={"location": harbour}).status_code == 404
    assert client.get("/app/reorder.csv", params={"location": harbour}).status_code == 404
    export = client.get("/app/reorder.csv")
    assert export.status_code == 200 and "Harbour oats" not in export.text
    with SessionLocal() as db:
        assert db.get(StockItem, oats).qty_base == Decimal("4")
    # An owner of the same business sees both locations.
    assert "Harbour oats" in sign_in("owner").get("/app").text
