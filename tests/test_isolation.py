"""T1.03 (AC-SAAS-01, SAAS-T-09): a member of one business never reaches another's data.

The cases are generated from the app's route table. Every route that takes an identifier is
called by a signed-in member of business A with business B's identifiers: it must answer 404 or
403, show nothing of B, and leave every row B owns exactly as it was. The same call with A's own
identifiers must succeed, so a route that refuses everything cannot pass by accident. A new
route that takes an identifier fails the coverage test until it has a case here.
"""

import hashlib
import typing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Document,
    DocumentLine,
    DocumentStatus,
    Invitation,
    Location,
    Membership,
    ProductIdentifier,
    Promotion,
    PromotionStatus,
    StockBatch,
    StockItem,
    Tenant,
    TrackMode,
    User,
)
from app.security import hash_password
from app.storage import get_storage
from app.units import Dimension
from tests.accounts import confirm_email
from tests.sample_files import jpeg
from tests.test_async_correctness import api_routes

# Integer parameters that are quantities, not identifiers. Any other integer is an identifier.
NOT_IDENTIFIERS = {"lead_time_days", "business_day_cutoff_hour"}


def _is_int(annotation) -> bool:
    return annotation is int or int in typing.get_args(annotation)


def identifier_params(route) -> list[str]:
    """Every path parameter, and every query or form field that names a row."""
    found = []

    def walk(dependant):
        for param in dependant.path_params:
            found.append(param.name)
        for param in dependant.query_params + dependant.body_params:
            names_a_row = param.name == "id" or param.name.endswith("_id")
            if names_a_row or (
                _is_int(param.field_info.annotation) and param.name not in NOT_IDENTIFIERS
            ):
                found.append(param.name)
        for sub in dependant.dependencies:
            walk(sub)

    walk(route.dependant)
    return sorted(set(found))


def routes_with_identifiers() -> dict[tuple[str, str], list[str]]:
    result = {}
    for route in api_routes(app.routes):
        params = identifier_params(route)
        if params:
            for method in route.methods:
                result[(method, route.path)] = params
    return result


# --- the cases: how to call each route for one business's rows ------------------------------------
# ids holds that business's rows: location, item, promo_item, batch, promotion, document, line.


def _apply_form(ids):
    line = ids["line"]
    return {
        "data": {
            f"line_{line}_action": "apply",
            f"line_{line}_item": ids["item"],
            f"line_{line}_qty": "2",
        }
    }


CASES = {
    ("GET", "/app"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/expiring"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/health"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/reorder"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/reorder.csv"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/promotions"): lambda ids: {"params": {"location": ids["location"]}},
    ("POST", "/app/expiring/label"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "quantity": "2", "expires_on": "2030-01-31"}
    },
    ("POST", "/app/expiring/perishable"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "perishable": "yes"}
    },
    ("POST", "/app/expiring/{batch_id}/remove"): lambda ids: {},
    ("POST", "/app/items/new"): lambda ids: {
        "data": {
            "location_id": ids["location"],
            "name": "Oat milk",
            "dimension": "count",
            "stock_unit": "each",
        }
    },
    ("POST", "/app/items/{item_id}/count"): lambda ids: {"data": {"quantity": "3"}},
    ("POST", "/app/items/{item_id}/thresholds"): lambda ids: {"data": {"reorder_point": "1"}},
    ("POST", "/app/receiving"): lambda ids: {
        "data": {"location_id": ids["location"]},
        "files": {"photo": ("delivery.jpg", jpeg("isolation"), "image/jpeg")},
    },
    ("GET", "/app/receiving/{document_id}"): lambda ids: {},
    ("GET", "/app/receiving/{document_id}/image"): lambda ids: {},
    ("POST", "/app/receiving/{document_id}/reparse"): lambda ids: {},
    ("POST", "/app/receiving/{document_id}/apply"): _apply_form,
    ("POST", "/app/promotions/accept"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "price": ""}
    },
    ("POST", "/app/promotions/dismiss"): lambda ids: {"data": {"stock_item_id": ids["item"]}},
    ("POST", "/app/promotions/snooze"): lambda ids: {"data": {"stock_item_id": ids["item"]}},
    ("POST", "/app/promotions/{promotion_id}/end"): lambda ids: {},
    ("POST", "/app/scan/link"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "code": "4006381333931"}
    },
    ("POST", "/app/scan/receive"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "quantity": "1"}
    },
    ("POST", "/app/scan/count"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "quantity": "5"}
    },
    ("GET", "/app/scan/codes/{stock_item_id}"): lambda ids: {},
    ("GET", "/app/locations/{location_id}"): lambda ids: {},
    ("POST", "/app/locations/{location_id}"): lambda ids: {
        "data": {
            "name": "Renamed",
            "timezone": "America/Chicago",
            "currency": "USD",
            "open_days": ["mon"],
        }
    },
    ("POST", "/app/locations/{location_id}/archive"): lambda ids: {},
    ("POST", "/app/organizations/switch"): lambda ids: {
        "data": {"membership_id": ids["membership"]}
    },
    ("POST", "/app/locations/{location_id}/restore"): lambda ids: {},
    ("POST", "/app/team/invite"): lambda ids: {
        "data": {
            "email": "new.hire@example.test",
            "role": "employee",
            "scope": "some",
            "location_ids": [ids["location"]],
        }
    },
    ("POST", "/app/team/{membership_id}/role"): lambda ids: {
        "data": {"role": "manager", "scope": "some", "location_ids": [ids["location"]]}
    },
    ("POST", "/app/team/{membership_id}/remove"): lambda ids: {},
    ("POST", "/app/team/invitations/{invitation_id}/withdraw"): lambda ids: {},
    ("GET", "/app/audit"): lambda ids: {"params": {"person": ids["user"]}},
}

PATH_ROWS = {
    "location_id": "location",
    "batch_id": "batch",
    "item_id": "item",
    "stock_item_id": "item",
    "document_id": "document",
    "promotion_id": "promotion",
    "membership_id": "membership",
    "invitation_id": "invitation",
}


def _request(client, method: str, path: str, ids: dict[str, int]):
    url = path.format(**{name: ids[row] for name, row in PATH_ROWS.items()})
    return client.request(method, url, follow_redirects=False, **CASES[(method, path)](ids))


# --- two businesses, each with one of everything --------------------------------------------------


def _business(db, tenant_id: int, tag: str, code: str) -> dict[str, int]:
    """A location, an item with a lot at risk of expiring, a running markdown, a delivery."""
    location = Location(
        tenant_id=tenant_id, provider="demo", provider_location_id=f"{tag}-1", name=f"{tag} Main"
    )
    db.add(location)
    db.flush()

    def item(slug: str, name: str) -> StockItem:
        row = StockItem(
            tenant_id=tenant_id,
            location_id=location.id,
            slug=slug,
            name=name,
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.MANUAL,
            qty_base=Decimal("24"),
            cost_per_base_cents=Decimal("80"),
            retail_per_base_cents=Decimal("249"),
        )
        db.add(row)
        db.flush()
        return row

    bars = item("bars", f"{tag} protein bars")
    brew = item("brew", f"{tag} cold brew")
    batch = StockBatch(
        tenant_id=tenant_id,
        stock_item_id=bars.id,
        received_qty_base=Decimal("24"),
        expires_on=date.today() + timedelta(days=9),
    )
    promotion = Promotion(
        tenant_id=tenant_id,
        stock_item_id=brew.id,
        status=PromotionStatus.ACTIVE,
        starts_on=date.today(),
    )
    db.add_all(
        [
            batch,
            promotion,
            ProductIdentifier(tenant_id=tenant_id, stock_item_id=bars.id, value=code),
        ]
    )

    photo = jpeg(f"{tag} delivery")
    digest = hashlib.sha256(photo).hexdigest()
    key = f"{tenant_id}/{digest}.jpg"
    get_storage().put(key, photo, "image/jpeg")
    document = Document(
        tenant_id=tenant_id,
        location_id=location.id,
        supplier=f"{tag} Foods",
        storage_key=key,
        content_type="image/jpeg",
        sha256=digest,
        byte_size=len(photo),
        status=DocumentStatus.NEEDS_REVIEW,
    )
    db.add(document)
    db.flush()
    line = DocumentLine(
        tenant_id=tenant_id,
        document_id=document.id,
        line_no=1,
        raw_text="2 cs bars",
        parsed_name="bars",
        parsed_qty=Decimal("2"),
    )
    db.add(line)
    db.flush()
    return {
        "tenant": tenant_id,
        "location": location.id,
        "item": bars.id,
        "promo_item": brew.id,
        "batch": batch.id,
        "promotion": promotion.id,
        "document": document.id,
        "line": line.id,
    }


def invitation(db, tenant_id: int, inviter_id: int) -> int:
    """A pending invitation to the business, sent by inviter_id."""
    row = Invitation(
        tenant_id=tenant_id,
        email=f"invited-{tenant_id}@example.test",
        role="employee",
        invited_by_user_id=inviter_id,
        token_hash=hashlib.sha256(f"invitation-{tenant_id}".encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(row)
    db.flush()
    return row.id


B_CODE, A_CODE = "990000000017", "110000000014"


@pytest.fixture
def world():
    """Business B exists first; the client is signed in as an owner of business A."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        juniper = Tenant(name="Juniper")
        db.add(juniper)
        db.flush()
        their_owner = User(
            tenant_id=juniper.id,
            email="owner@juniper.test",
            password_hash=hash_password("another-good-password"),
        )
        db.add(their_owner)
        db.flush()
        their_membership = Membership(tenant_id=juniper.id, user_id=their_owner.id, role="owner")
        db.add(their_membership)
        db.flush()
        theirs = _business(db, juniper.id, "Juniper", B_CODE)
        theirs["membership"] = their_membership.id
        theirs["invitation"] = invitation(db, juniper.id, their_owner.id)
        theirs["user"] = their_owner.id
        db.commit()
    with TestClient(app) as client:
        client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "owner@rosewood.test",
                "password": "a-good-password",
            },
        )
        confirm_email("owner@rosewood.test")
        with SessionLocal() as db:
            rosewood = db.scalars(select(Tenant).where(Tenant.name == "Rosewood")).one()
            mine = _business(db, rosewood.id, "Rosewood", A_CODE)
            own = db.scalars(select(Membership).where(Membership.tenant_id == rosewood.id)).one()
            mine["membership"] = own.id
            mine["invitation"] = invitation(db, rosewood.id, own.user_id)
            mine["user"] = own.user_id
            db.commit()
        yield client, mine, theirs


def snapshot(tenant_id: int) -> dict[str, list[tuple]]:
    """Every row the business owns, column by column (the test looks across businesses)."""
    rows = {}
    with SessionLocal() as db:
        for table in Base.metadata.sorted_tables:
            if table.name == "tenants":
                query = select(table).where(table.c.id == tenant_id)
            elif "tenant_id" in table.c:
                query = select(table).where(table.c.tenant_id == tenant_id)
            else:
                continue
            rows[table.name] = sorted(tuple(map(str, row)) for row in db.execute(query))
    return rows


# --- the suite ------------------------------------------------------------------------------------


def test_every_route_that_takes_an_identifier_has_a_case():
    """A new id route fails here until it is added to CASES, and so to the suite."""
    routes = routes_with_identifiers()
    assert len(routes) >= 25, "the route walk must reach the routers' routes"
    assert set(routes) - set(CASES) == set(), "add a case for each new route"
    assert set(CASES) - set(routes) == set(), "remove cases for routes that no longer exist"


@pytest.mark.parametrize(("method", "path"), sorted(CASES), ids=lambda part: str(part))
def test_another_business_identifiers_reach_nothing(world, method, path):
    client, mine, theirs = world
    before = snapshot(theirs["tenant"])

    response = _request(client, method, path, theirs)

    assert response.status_code in (403, 404), response.text[:300]
    assert "Juniper" not in response.text, "the refusal must not describe the other business"
    assert snapshot(theirs["tenant"]) == before, "the other business's rows changed"

    # The same request with the member's own identifiers works, so the refusal above was about
    # whose rows they were, not a broken case.
    own = _request(client, method, path, mine)
    assert own.status_code in (200, 303), own.text[:300]


def test_another_business_barcode_finds_nothing(world):
    """Codes identify products too: B's barcode means nothing to A's scanner."""
    client, _mine, theirs = world
    before = snapshot(theirs["tenant"])

    theirs_found = client.get("/app/scan/lookup", params={"code": B_CODE})
    mine_found = client.get("/app/scan/lookup", params={"code": A_CODE})

    assert theirs_found.status_code == 200 and theirs_found.json()["found"] is False
    assert "Juniper" not in theirs_found.text
    assert mine_found.json()["found"] is True
    assert snapshot(theirs["tenant"]) == before


def test_a_delivery_cannot_be_received_into_another_business_item(world):
    """A's own invoice, with a line pointed at B's item id: nothing of B's moves."""
    client, mine, theirs = world
    before = snapshot(theirs["tenant"])
    line = mine["line"]

    response = client.post(
        f"/app/receiving/{mine['document']}/apply",
        data={
            f"line_{line}_action": "apply",
            f"line_{line}_item": theirs["item"],
            f"line_{line}_qty": "50",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "applied=0" in response.headers["location"]
    assert snapshot(theirs["tenant"]) == before


def test_the_snapshot_sees_a_change(world):
    """Guards the guard: a change to B's rows is detected."""
    _client, _mine, theirs = world
    before = snapshot(theirs["tenant"])
    with SessionLocal() as db:
        db.get(StockItem, theirs["item"]).qty_base = Decimal("23")
        db.commit()
    assert snapshot(theirs["tenant"]) != before
