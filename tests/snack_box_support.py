"""Shared setup for the snack box tests: a store with boxable, dated stock, shoppers, and a fake Square."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import Base, engine
from app.main import app
from app.models import BoxLine, BoxOrder, SnackProfile, StockBatch, StockItem, TrackMode
from app.orchestration import boxes, customers
from app.units import Dimension

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
FLAVOR_CYCLE = ("sweet", "salty", "spicy", "weird")
ORIGINS = ("US", "JP", "KR", "MX")


class FakeSquare:
    """Square's IN_STOCK counts by variation. Idempotency keys behave like Square's; it can go down."""

    def __init__(self) -> None:
        self.counts: dict[str, Decimal] = {}
        self.keys: set[str] = set()
        self.moves: list[tuple] = []
        self.down = False
        self.unreadable = False

    def move(self, db, tenant_id, item, from_state, to_state, qty_base, key) -> bool:
        if item.track_mode is not TrackMode.PROVIDER or not item.provider_variation_id:
            return False
        if self.down:
            raise RuntimeError("square is down")
        if key in self.keys:
            return True
        self.keys.add(key)
        self.moves.append(
            (item.provider_variation_id, from_state, to_state, Decimal(qty_base), key)
        )
        if from_state == "IN_STOCK":
            self.counts[item.provider_variation_id] -= Decimal(qty_base)
        if to_state == "IN_STOCK":
            self.counts[item.provider_variation_id] += Decimal(qty_base)
        return True

    def refresh(self, db, tenant_id, items) -> bool:
        if self.unreadable:
            return False
        for item in items:
            if item.provider_variation_id in self.counts:
                item.qty_base = self.counts[item.provider_variation_id]
        db.flush()
        return True

    def sell_at_till(self, variation_id: str, units: int) -> None:
        self.counts[variation_id] -= Decimal(units)


def install_fake_square(monkeypatch) -> FakeSquare:
    fake = FakeSquare()
    monkeypatch.setattr(boxes, "square_move", fake.move)
    monkeypatch.setattr(boxes, "square_refresh", fake.refresh)
    monkeypatch.setattr(boxes, "square_checkout", lambda db, order, customer: None)
    return fake


@pytest.fixture
def square(monkeypatch) -> FakeSquare:
    return install_fake_square(monkeypatch)


@pytest.fixture
def web():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def outbox(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    def capture(db, tenant_id, to_email, subject, html, kind, text=None):
        sent.append({"to": to_email, "subject": subject, "text": text or "", "kind": kind})
        return True

    monkeypatch.setattr(customers, "send_email", capture)
    return sent


def allow_snack_shop(tenant_id: int) -> None:
    """Turn the private snack box module on for one account, the way the environment does in production."""
    settings = get_settings()
    allowed = {part for part in (settings.snack_box_tenant_ids or "").split(",") if part}
    settings.snack_box_tenant_ids = ",".join(sorted(allowed | {str(tenant_id)}))


def open_store(
    db,
    tenant,
    location,
    fake: FakeSquare,
    n_items: int = 8,
    qty: int = 10,
    cost_cents: int = 100,
    days: int = 120,
) -> list[StockItem]:
    """An open box shop with n boxable, dated, Square-tracked snacks in four categories and flavours."""
    allow_snack_shop(tenant.id)
    settings = boxes.box_settings(db, tenant.id)
    settings.is_open = True
    settings.location_id = location.id
    items = []
    for i in range(n_items):
        item = StockItem(
            tenant_id=tenant.id,
            location_id=location.id,
            slug=f"snack-{i}",
            name=f"Snack {i}",
            category=f"Cat{i % 4}",
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.PROVIDER,
            provider_variation_id=f"VAR-{tenant.id}-{i}",
            qty_base=Decimal(qty),
            cost_per_base_cents=Decimal(cost_cents),
        )
        db.add(item)
        db.flush()
        db.add(
            StockBatch(
                tenant_id=tenant.id,
                stock_item_id=item.id,
                received_qty_base=Decimal(qty),
                expires_on=NOW.date() + timedelta(days=days),
                source="label",
            )
        )
        db.add(
            SnackProfile(
                tenant_id=tenant.id,
                stock_item_id=item.id,
                boxable=True,
                flavors=FLAVOR_CYCLE[i % 4],
                origin=ORIGINS[i % 4],
                allergens="",
            )
        )
        fake.counts[item.provider_variation_id] = Decimal(qty)
        items.append(item)
    db.flush()
    return items


def shopper(
    db, tenant, email: str = "maya@shop.test", allergens: frozenset[str] = frozenset(), now=NOW
):
    customer = customers.find_or_create(db, tenant.id, email, now)
    customers.update_preferences(db, customer, allergens=allergens, now=now)
    return customer


def paid_box(
    db, tenant, customer, box_type: str = "chaos", now=NOW, code: str = "", use_credit: bool = False
) -> BoxOrder:
    order = boxes.place_order(db, tenant.id, customer, box_type, code, use_credit, now)
    boxes.confirm_payment(db, order, "counter", now=now)
    return order


def lines_of(db, order: BoxOrder) -> list[BoxLine]:
    return list(
        db.scalars(select(BoxLine).where(BoxLine.box_order_id == order.id).order_by(BoxLine.id))
    )


def pack_all(db, order: BoxOrder, now=NOW) -> list[str]:
    return boxes.pack(db, order, {line.id for line in lines_of(db, order)}, now)


def sign_in(client, outbox: list[dict], tenant_id: int, email: str):
    """Ask for a link, then press the button on it. Returns (response, raw token)."""
    client.post(f"/shop/{tenant_id}/login", data={"email": email})
    url = re.search(r"https?://\S+", outbox[-1]["text"]).group(0)
    token = url.split("token=", 1)[1]
    return client.post(
        f"/shop/{tenant_id}/auth", data={"token": token}, follow_redirects=False
    ), token
