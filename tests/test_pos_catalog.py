"""T3.03: location mapping and the external catalog (POS-FR-03, POS-FR-04, SQ-05..09, G11, DM-13).

Square is replaced at its client with recorded-shape payloads; nothing reaches the network.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.access import OrgContext
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    AuditEvent,
    ExternalLocation,
    ExternalModifier,
    ExternalProduct,
    Location,
    ProviderConnection,
    Tenant,
)
from app.orchestration import sync
from app.pos_catalog import MappingError, NotFound, Summary, summary, track_location
from tests.accounts import confirm_email

CATALOG = [
    {"type": "CATEGORY", "id": "cat-1", "category_data": {"name": "Drinks"}},
    {
        "type": "MEASUREMENT_UNIT",
        "id": "mu-mg",
        "measurement_unit_data": {"measurement_unit": {"weight_unit": "METRIC_MILLIGRAM"}},
    },
    {
        "type": "ITEM",
        "id": "item-latte",
        "item_data": {
            "name": "Latte",
            "category_id": "cat-1",
            "variations": [
                {
                    "id": "var-12",
                    "item_variation_data": {
                        "name": "12 oz",
                        "price_money": {"amount": 450, "currency": "USD"},
                    },
                },
                {
                    "id": "var-16",
                    "item_variation_data": {
                        "name": "16 oz",
                        "price_money": {"amount": 525, "currency": "USD"},
                    },
                },
            ],
        },
    },
    {
        "type": "ITEM",
        "id": "item-saffron",
        "item_data": {
            "name": "Saffron",
            "variations": [
                {
                    "id": "var-saffron",
                    "item_variation_data": {
                        "name": "By weight",
                        "measurement_unit_id": "mu-mg",
                        "price_money": {"amount": 12, "currency": "USD"},
                    },
                }
            ],
        },
    },
    {
        "type": "MODIFIER_LIST",
        "id": "ml-milk",
        "modifier_list_data": {
            "name": "Milk",
            "modifiers": [
                {"id": "mod-oat", "modifier_data": {"name": "Oat"}},
                {"id": "mod-whole", "modifier_data": {"name": "Whole"}},
            ],
        },
    },
]
PLACES = [
    {
        "id": "SQ-1",
        "name": "Main St",
        "timezone": "America/New_York",
        "address": {"address_line_1": "1 Main St", "locality": "Springfield"},
    },
    {"id": "SQ-2", "name": "Old kiosk", "timezone": "UTC", "status": "INACTIVE"},
]


class FakeSquare:
    """The client calls a sync makes: what it was asked, and for which locations."""

    def __init__(self):
        self.places = list(PLACES)
        self.objects = list(CATALOG)
        self.types: tuple[str, ...] = ()
        self.order_locations: list[list[str]] = []

    def __call__(self, _token):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def locations(self):
        return self.places

    def catalog_objects(self, types):
        self.types = types
        return self.objects

    def inventory_counts(self, *_args):
        return []

    def orders(self, location_ids, _start, _end):
        self.order_locations.append(list(location_ids))
        return []


@pytest.fixture
def square(db, tenant, location, monkeypatch):
    """A connected business whose one location was added by its owner (it tracks nothing)."""
    location.provider, location.provider_location_id = None, None
    connection = ProviderConnection(
        tenant_id=tenant.id, provider="square", merchant_id="M-1", access_token_enc="enc"
    )
    db.add(connection)
    db.commit()
    fake = FakeSquare()
    monkeypatch.setattr(sync, "SquareClient", fake)
    monkeypatch.setattr(sync, "_access_token", lambda *_: "token")
    return connection, fake


def org_of(tenant) -> OrgContext:
    return OrgContext.system(tenant.id)


def place(db, external_id: str) -> ExternalLocation:
    return db.scalars(
        select(ExternalLocation).where(ExternalLocation.external_location_id == external_id)
    ).one()


# --- the catalog (POS-FR-04, SQ-06..09) ----------------------------------------------------------


def test_a_catalog_with_modifiers_imports_with_its_units_and_a_resync_changes_nothing(
    db, tenant, square
):
    connection, fake = square
    sync.sync_catalog(db, org_of(tenant))
    db.commit()
    assert "MODIFIER_LIST" in fake.types

    products = {p.external_variation_id: p for p in db.scalars(select(ExternalProduct))}
    assert set(products) == {"var-12", "var-16", "var-saffron"}
    latte = products["var-12"]
    assert (latte.external_item_id, latte.name, latte.variation_name, latte.category) == (
        "item-latte",
        "Latte",
        "12 oz",
        "Drinks",
    )
    assert (latte.price_cents, latte.unit, latte.connection_id) == (450, "each", connection.id)
    assert products["var-saffron"].unit == "mg", "a milligram is not a gram (audit SQ8)"
    modifiers = {(m.list_name, m.name) for m in db.scalars(select(ExternalModifier))}
    assert modifiers == {("Milk", "Oat"), ("Milk", "Whole")}
    assert summary(db, org_of(tenant), connection) == Summary(
        items=2, variations=3, modifiers=2, locations=1, tracked=0
    )

    again = sync.sync_catalog(db, org_of(tenant))
    db.commit()
    assert {k: v for k, v in again.items() if k.startswith(("products_", "modifiers_"))} == {
        f"{kind}_{change}": 0
        for kind in ("products", "modifiers")
        for change in ("created", "changed", "deactivated")
    }
    assert again["locations"] == 0
    assert db.scalar(select(func.count()).select_from(ExternalProduct)) == 3


def test_what_square_stops_listing_is_kept_inactive_and_comes_back(db, tenant, square):
    """SQ-08: an archived or deleted variation keeps its row, so a mapping and history stay."""
    _connection, fake = square
    sync.sync_catalog(db, org_of(tenant))
    latte = next(o for o in fake.objects if o["id"] == "item-latte")
    fake.objects = [o for o in fake.objects if o["id"] not in {"item-latte", "ml-milk"}] + [
        {
            **latte,
            "item_data": {**latte["item_data"], "variations": latte["item_data"]["variations"][:1]},
        }
    ]
    sync.sync_catalog(db, org_of(tenant))
    db.commit()
    active = {p.external_variation_id: p.active for p in db.scalars(select(ExternalProduct))}
    assert active == {"var-12": True, "var-16": False, "var-saffron": True}
    assert not any(m.active for m in db.scalars(select(ExternalModifier)))

    fake.objects = list(CATALOG)
    sync.sync_catalog(db, org_of(tenant))
    db.commit()
    assert all(p.active for p in db.scalars(select(ExternalProduct)))


# --- locations (POS-FR-03, SQ-05, G11) -----------------------------------------------------------


def test_g11_the_sync_records_square_locations_and_never_creates_one(db, tenant, square):
    _connection, fake = square
    before = db.scalar(select(func.count()).select_from(Location))
    sync.run_sync(db, org_of(tenant))
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Location)) == before
    main, kiosk = place(db, "SQ-1"), place(db, "SQ-2")
    assert (main.name, main.timezone, main.active) == ("Main St", "America/New_York", True)
    assert main.address and "1 Main St" in main.address
    assert kiosk.active is False
    assert fake.order_locations == [], "no location tracks Square's: no sales are read"


def test_pos_fr_03_a_square_location_is_tracked_by_one_of_the_business_locations(
    db, tenant, location, square
):
    connection, fake = square
    org = org_of(tenant)
    sync.sync_catalog(db, org)
    kitchen = Location(tenant_id=tenant.id, name="Kitchen", timezone="UTC")
    db.add(kitchen)
    db.commit()
    main, kiosk = place(db, "SQ-1"), place(db, "SQ-2")

    assert track_location(db, org, main.id, location.id) is True
    assert (location.provider, location.provider_location_id) == ("square", "SQ-1")
    assert track_location(db, org, main.id, location.id) is False
    with pytest.raises(MappingError, match="already tracks"):
        track_location(db, org, kiosk.id, location.id)
    db.commit()
    sync.run_sync(db, org)
    assert fake.order_locations == [["SQ-1"]], "the tracked location's sales are read"

    assert track_location(db, org, main.id, kitchen.id) is True  # moved
    assert location.provider_location_id is None and kitchen.provider_location_id == "SQ-1"
    assert track_location(db, org, main.id, None) is True
    assert kitchen.provider_location_id is None
    assert track_location(db, org, main.id, None) is False
    db.commit()
    actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id))]
    assert actions == [
        "connection.location_tracked",
        "connection.location_tracked",
        "connection.location_untracked",
    ]

    kitchen.archived_at, kitchen.is_active = datetime.now(UTC), False
    with pytest.raises(MappingError, match="not in use"):
        track_location(db, org, main.id, kitchen.id)
    other = Tenant(name="Harbor")
    db.add(other)
    db.flush()
    theirs = Location(tenant_id=other.id, name="Theirs", timezone="UTC")
    db.add(theirs)
    db.flush()
    with pytest.raises(NotFound):
        track_location(db, org, main.id, theirs.id)
    with pytest.raises(NotFound):
        track_location(db, org_of(other), main.id, theirs.id)
    connection.status, connection.access_token_enc = "disconnected", None
    with pytest.raises(MappingError, match="Reconnect"):
        track_location(db, org, main.id, location.id)


# --- the screen ----------------------------------------------------------------------------------


@pytest.fixture
def owner(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client = TestClient(app)
    client.__enter__()
    client.post(
        "/signup",
        data={
            "business_name": "Corner Cafe",
            "email": "owner@corner.test",
            "password": "a-good-password",
        },
    )
    confirm_email("owner@corner.test")
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        db.add(Location(tenant_id=tenant.id, name="Corner Main", timezone="UTC"))
        db.add(
            ProviderConnection(
                tenant_id=tenant.id, provider="square", merchant_id="M-1", access_token_enc="enc"
            )
        )
        db.commit()
        fake = FakeSquare()
        monkeypatch.setattr(sync, "SquareClient", fake)
        monkeypatch.setattr(sync, "_access_token", lambda *_: "token")
        sync.sync_catalog(db, OrgContext.system(tenant.id))
        db.commit()
    yield client
    client.__exit__(None, None, None)


def test_the_connection_screen_shows_the_import_and_maps_a_location(owner):
    page = owner.get("/app/connection").text
    assert "2 items, 3 variations and 2 modifiers imported" in page
    assert "Main St" in page and "(closed in Square)" in page
    with SessionLocal() as db:
        main = place(db, "SQ-1")
        corner = db.scalars(select(Location).where(Location.name == "Corner Main")).one()

    saved = owner.post(f"/app/connection/locations/{main.id}", data={"location_id": corner.id})
    assert "Saved." in saved.text
    with SessionLocal() as db:
        assert db.get(Location, corner.id).provider_location_id == "SQ-1"
    cleared = owner.post(f"/app/connection/locations/{main.id}", data={"location_id": ""})
    assert "Saved." in cleared.text
    with SessionLocal() as db:
        assert db.get(Location, corner.id).provider_location_id is None
    assert owner.post("/app/connection/locations/9999", data={"location_id": ""}).status_code == 404


# --- the rules -----------------------------------------------------------------------------------


def test_only_the_catalog_service_writes_what_a_pos_reports_and_which_location_tracks_it():
    writes = re.compile(
        r"External(Location|Product|Modifier)\(|\.provider_location_id\s*=[^=]"
        r"|update_scoped\(\w+, External"
    )
    offenders = [
        path.as_posix()
        for path in Path("app").rglob("*.py")
        if path.as_posix() not in {"app/pos_catalog.py", "app/models.py"}
        and writes.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
