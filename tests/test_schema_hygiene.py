"""T1.01 (DM-2): instants are UTC-aware, CHECKs refuse impossible rows, deletes follow rules."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import DateTime, delete, func, select
from sqlalchemy.exc import IntegrityError, StatementError

from app.db import Base, SessionLocal, UTCDateTime, engine
from app.main import app
from app.models import (
    Document,
    DocumentLine,
    EmailSuppression,
    ExtractionCorrection,
    InventoryCount,
    Location,
    ProductIdentifier,
    RecipeComponent,
    SoldItem,
    StockBatch,
    StockItem,
    Tenant,
)
from app.units import Dimension


def _item(db, tenant, location, **values) -> StockItem:
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug=values.pop("slug", "milk"),
        name="Milk",
        dimension=Dimension.COUNT,
        stock_unit="each",
        **values,
    )
    db.add(item)
    db.flush()
    return item


# --- timestamps (DR-20) ---------------------------------------------------------------------------


def test_a_stored_instant_comes_back_aware_and_in_utc(db, tenant):
    """Probe PA5 inverted: the database no longer hands back naive datetimes."""
    tenant.trial_started_at = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    # The same instant written from another zone is the same instant read back.
    tenant.trial_ends_at = datetime(2026, 11, 18, 21, 0, tzinfo=timezone(timedelta(hours=9)))
    db.commit()
    db.expire_all()

    stored = db.get(Tenant, tenant.id)
    assert stored.trial_started_at == datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    assert stored.trial_started_at.utcoffset() == timedelta(0)
    assert stored.trial_ends_at == datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    assert stored.trial_ends_at.utcoffset() == timedelta(0)
    assert stored.created_at.utcoffset() == timedelta(0)


def test_a_naive_datetime_is_refused_rather_than_guessed(db, tenant):
    tenant.trial_started_at = datetime(2026, 11, 4, 12, 0)  # naive on purpose
    with pytest.raises(StatementError, match="no time zone"):
        db.commit()
    db.rollback()


def test_instants_compare_in_queries(db, tenant):
    tenant.trial_ends_at = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    db.commit()
    later = datetime(2026, 11, 18, 13, 0, tzinfo=timezone(timedelta(hours=2)))  # 11:00 UTC
    assert db.scalar(select(func.count()).where(Tenant.trial_ends_at > later)) == 1


@pytest.mark.parametrize("stamp", ["2026-12-01T00:00:00Z", "2026-12-01T00:00:00"])
def test_square_token_expiry_is_stored_as_an_aware_instant(stamp):
    """An expiry without an offset would be refused by the column; it is read as UTC instead."""
    from app.providers.square.oauth import _normalize_token

    token = _normalize_token({"access_token": "t", "expires_at": stamp})
    assert token["expires_at"] == datetime(2026, 12, 1, tzinfo=UTC)


def test_every_timestamp_column_is_an_aware_instant():
    plain = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, DateTime) and not isinstance(column.type, UTCDateTime)
    ]
    assert plain == []
    aware = [
        column
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, UTCDateTime)
    ]
    assert len(aware) == 31


# --- CHECK constraints (DB1) ----------------------------------------------------------------------


def _batch(tenant, item, **values) -> StockBatch:
    return StockBatch(
        tenant_id=tenant.id,
        stock_item_id=item.id,
        received_qty_base=values.pop("received_qty_base", Decimal("5")),
        expires_on=date(2026, 12, 1),
        **values,
    )


IMPOSSIBLE = {
    "negative reorder point": lambda t, i: setattr(i, "reorder_point_base", Decimal("-1")),
    "negative par level": lambda t, i: setattr(i, "par_level_base", Decimal("-0.5")),
    "empty pack": lambda t, i: setattr(i, "pack_size_base", Decimal("0")),
    "negative lead time": lambda t, i: setattr(i, "lead_time_days", -1),
    "empty batch": lambda t, i: _batch(t, i, received_qty_base=Decimal("0")),
    "negative batch": lambda t, i: _batch(t, i, received_qty_base=Decimal("-3")),
    "unknown batch source": lambda t, i: _batch(t, i, source="snack_box"),
    "unknown count source": lambda t, i: InventoryCount(
        tenant_id=t.id, stock_item_id=i.id, qty_base=Decimal("1"), source="square_sync"
    ),
    "unknown code source": lambda t, i: ProductIdentifier(
        tenant_id=t.id, stock_item_id=i.id, value="0123", source="guess"
    ),
    "unknown suppression source": lambda t, i: EmailSuppression(email="a@b.test", source="unknown"),
}


@pytest.mark.parametrize("case", sorted(IMPOSSIBLE))
def test_the_database_refuses_an_impossible_row(db, tenant, location, case):
    item = _item(db, tenant, location)
    db.commit()
    row = IMPOSSIBLE[case](tenant, item)
    if row is not None:
        db.add(row)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_a_recipe_must_consume_something(db, tenant, location):
    item = _item(db, tenant, location)
    sold = SoldItem(tenant_id=tenant.id, provider="demo", provider_item_id="latte", name="Latte")
    db.add(sold)
    db.flush()
    db.add(
        RecipeComponent(
            tenant_id=tenant.id,
            sold_item_id=sold.id,
            stock_item_id=item.id,
            qty_base_per_sale=Decimal("0"),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_what_the_rules_allow_is_still_accepted(db, tenant, location):
    """A recorded balance may go negative (DR-08); minus zero is zero; nulls mean not set."""
    item = _item(
        db,
        tenant,
        location,
        qty_base=Decimal("-4"),
        reorder_point_base=Decimal("-0.00001"),  # quantized to -0.0000
        par_level_base=None,
        pack_size_base=None,
        lead_time_days=0,
    )
    db.add(_batch(tenant, item, source="scan"))
    db.add(
        InventoryCount(
            tenant_id=tenant.id, stock_item_id=item.id, qty_base=Decimal("-4"), source="receipt"
        )
    )
    db.add(EmailSuppression(email="a@b.test", source="complaint"))
    db.commit()
    db.expire_all()
    assert db.get(StockItem, item.id).qty_base == Decimal("-4")


# --- delete rules ---------------------------------------------------------------------------------


def test_a_reference_to_a_missing_row_is_refused(db, tenant):
    """SQLite enforces foreign keys too, so both databases behave alike."""
    db.add(Location(tenant_id=tenant.id + 99, provider="demo", provider_location_id="x", name="X"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_deleting_referenced_master_data_is_refused(db, tenant, location):
    """DR-24: an item with history is deactivated, never deleted."""
    item = _item(db, tenant, location)
    db.add(InventoryCount(tenant_id=tenant.id, stock_item_id=item.id, qty_base=Decimal("1")))
    db.commit()

    with pytest.raises(IntegrityError):
        db.execute(delete(StockItem).where(StockItem.id == item.id))
        db.commit()
    db.rollback()

    with pytest.raises(IntegrityError):
        db.execute(delete(Tenant).where(Tenant.id == tenant.id))
        db.commit()
    db.rollback()
    assert db.get(StockItem, item.id) is not None


def test_deleting_a_document_takes_its_lines_and_corrections(db, tenant, location):
    document = Document(
        tenant_id=tenant.id, location_id=location.id, storage_key="1/a.jpg", sha256="a" * 64
    )
    db.add(document)
    db.flush()
    line = DocumentLine(
        tenant_id=tenant.id, document_id=document.id, raw_text="2 cs milk", parsed_name="milk"
    )
    db.add(line)
    db.flush()
    db.add(
        ExtractionCorrection(
            tenant_id=tenant.id, document_id=document.id, document_line_id=line.id, field="quantity"
        )
    )
    db.commit()

    # Core delete: the database's rule does the work, not the ORM's cascade.
    db.execute(delete(Document).where(Document.id == document.id))
    db.commit()
    assert db.scalar(select(func.count()).select_from(DocumentLine)) == 0
    assert db.scalar(select(func.count()).select_from(ExtractionCorrection)) == 0


def test_deleting_a_sold_item_takes_its_recipe(db, tenant, location):
    item = _item(db, tenant, location)
    sold = SoldItem(tenant_id=tenant.id, provider="demo", provider_item_id="latte", name="Latte")
    db.add(sold)
    db.flush()
    db.add(
        RecipeComponent(
            tenant_id=tenant.id,
            sold_item_id=sold.id,
            stock_item_id=item.id,
            qty_base_per_sale=Decimal("200"),
        )
    )
    db.commit()

    db.execute(delete(SoldItem).where(SoldItem.id == sold.id))
    db.commit()
    assert db.scalar(select(func.count()).select_from(RecipeComponent)) == 0
    assert db.get(StockItem, item.id) is not None


# --- forms say no before the database has to ------------------------------------------------------


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        test_client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "owner@rosewood.test",
                "password": "a-good-password",
            },
        )
        with SessionLocal() as db:
            tenant = db.query(Tenant).one()
            location = Location(
                tenant_id=tenant.id, provider="demo", provider_location_id="l1", name="Main"
            )
            db.add(location)
            db.flush()
            _item(db, tenant, location, reorder_point_base=Decimal("2"))
            db.commit()
        yield test_client


@pytest.mark.parametrize(
    "form",
    [
        {"reorder_point": "-1"},
        {"reorder_point": "1", "par_level": "-3"},
        {"reorder_point": "1", "lead_time_days": "-2"},
        {"reorder_point": "lots"},
    ],
)
def test_negative_thresholds_are_refused_with_a_reason(client, form):
    response = client.post("/app/items/1/thresholds", data=form, follow_redirects=False)
    assert response.status_code == 400
    with SessionLocal() as db:
        assert db.get(StockItem, 1).reorder_point_base == Decimal("2")


def test_a_new_item_with_an_empty_pack_is_refused(client):
    response = client.post(
        "/app/items/new",
        data={
            "name": "Oat milk",
            "location_id": "1",
            "dimension": "count",
            "stock_unit": "each",
            "pack_size": "0",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "pack size above zero" in response.text
    with SessionLocal() as db:
        assert db.query(StockItem).count() == 1
