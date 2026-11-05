"""Catalog changes follow Square, without deleting anything of ours."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.load.upsert import sync_catalog_presence
from app.models import (
    InventoryCount,
    Location,
    ProductIdentifier,
    ProviderConnection,
    SoldItem,
    StockItem,
    SyncRun,
    TrackMode,
)
from app.orchestration import sync
from app.units import Dimension


def catalog_object(variation_id: str, name: str, sku: str = "SKU1") -> dict:
    return {
        "type": "ITEM",
        "id": f"ITEM-{variation_id}",
        "item_data": {
            "name": name,
            "variations": [
                {
                    "id": variation_id,
                    "type": "ITEM_VARIATION",
                    "item_variation_data": {
                        "name": "12oz",
                        "sku": sku,
                        "price_money": {"amount": 150, "currency": "USD"},
                        "track_inventory": True,
                    },
                }
            ],
        },
    }


class FakeSquareCatalog:
    """The two calls a catalog pull makes, and a count of how often it made them."""

    def __init__(self, objects: list[dict], counts: dict | None = None):
        self.objects = objects
        self.counts = counts or {}
        self.pulls = 0

    def __call__(self, _token):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def locations(self) -> list[dict]:
        return [{"id": "LOC-SQ-1", "name": "Main", "timezone": "UTC", "currency": "USD"}]

    def catalog_objects(self, _types) -> list[dict]:
        self.pulls += 1
        return self.objects

    def inventory_counts(self, _variation_ids, _location_ids) -> list[dict]:
        return [
            {
                "catalog_object_id": variation,
                "location_id": "LOC-SQ-1",
                "state": "IN_STOCK",
                "quantity": str(quantity),
                "calculated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
            for variation, quantity in self.counts.items()
        ]

    def orders(self, _location_ids, _start, _end) -> list[dict]:
        return []


@pytest.fixture
def connected(db, tenant, location, monkeypatch):
    """A connected tenant whose Square catalog holds one tracked variation."""
    db.add(
        ProviderConnection(
            tenant_id=tenant.id,
            provider="square",
            merchant_id="M-1",
            access_token_enc="enc",
            status="active",
        )
    )
    db.commit()
    fake = FakeSquareCatalog([catalog_object("VAR-COKE", "Coca-Cola")])
    monkeypatch.setattr(sync, "SquareClient", fake)
    monkeypatch.setattr(sync, "_access_token", lambda _db, _connection: "token")
    return fake


def stock_for(db, variation: str) -> StockItem | None:
    return db.query(StockItem).filter(StockItem.provider_variation_id == variation).first()


def test_a_catalog_pull_creates_the_stock_item_its_sku_and_its_sold_item(db, tenant, connected):
    stats = sync.sync_catalog(db, tenant.id)
    db.commit()

    assert stats["stock_items_created"] == 1
    item = stock_for(db, "VAR-COKE")
    assert item is not None and item.track_mode is TrackMode.PROVIDER and item.is_active
    assert db.query(SoldItem).one().provider_item_id == "VAR-COKE"
    assert db.query(ProductIdentifier).one().value == "SKU1"
    run = db.query(SyncRun).one()
    assert run.kind == "catalog" and run.status.value == "success"


def test_a_variation_deleted_in_square_is_hidden_and_its_history_is_kept(db, tenant, connected):
    sync.sync_catalog(db, tenant.id)
    db.commit()
    item = stock_for(db, "VAR-COKE")
    db.add(
        InventoryCount(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            qty_base=Decimal("12"),
            source="manual",
            counted_at=datetime.now(timezone.utc),
        )
    )
    db.commit()

    connected.objects = []  # the merchant deleted it in Square
    sync.sync_catalog(db, tenant.id)
    db.commit()

    db.refresh(item)
    assert item.is_active is False, "a deleted variation must drop out of the shelf"
    assert db.query(SoldItem).one().is_active is False
    assert db.query(StockItem).count() == 1, "the row itself is never deleted"
    assert db.query(InventoryCount).count() == 1, "nor is anything that happened to it"


def test_restoring_it_in_square_brings_it_back(db, tenant, connected):
    sync.sync_catalog(db, tenant.id)
    connected.objects = []
    sync.sync_catalog(db, tenant.id)
    connected.objects = [catalog_object("VAR-COKE", "Coca-Cola")]
    sync.sync_catalog(db, tenant.id)
    db.commit()

    assert stock_for(db, "VAR-COKE").is_active is True
    assert db.query(SoldItem).one().is_active is True


def test_stock_the_operator_defined_is_not_touched_by_a_catalog_change(db, tenant, location):
    """Flour is not in Square's catalog and never should be hidden because of it."""
    flour = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="flour",
        name="Pastry flour",
        dimension=Dimension.WEIGHT,
        stock_unit="kg",
        track_mode=TrackMode.DERIVED,
        qty_base=Decimal("45000"),
    )
    db.add(flour)
    db.commit()

    assert sync_catalog_presence(db, tenant.id, []) == 0
    db.refresh(flour)
    assert flour.is_active is True


def test_the_hourly_sync_recovers_a_sale_whose_webhook_never_arrived(db, tenant, connected):
    """Webhooks are the fast path; this run is what makes the numbers right when one is lost."""
    sync.sync_catalog(db, tenant.id)
    db.commit()
    item = stock_for(db, "VAR-COKE")
    item.qty_base = Decimal("24")  # what we hold; Square has since sold four
    db.commit()

    connected.counts = {"VAR-COKE": 20}
    stats = sync.run_sync(db, tenant.id)
    db.commit()

    db.refresh(item)
    assert stats["counts"] == 1
    assert item.qty_base == Decimal("20"), "the missed sale has to show up at reconciliation"
    assert item.last_synced_at is not None


def test_a_catalog_event_re_reads_the_catalog_once_for_a_burst(db, tenant, connected):
    """Square sends an event per change; a burst of edits must not mean a pull per edit."""
    from app.web.webhooks import _catalog_read_recently

    sync.sync_catalog(db, tenant.id)
    db.commit()
    assert connected.pulls == 1
    assert _catalog_read_recently(db, tenant.id) is True

    run = db.query(SyncRun).order_by(SyncRun.started_at.desc()).first()
    run.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db.commit()
    assert _catalog_read_recently(db, tenant.id) is False
