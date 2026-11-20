"""T3.01: the POS adapter contract, its registry, and Square as its first implementation
(POS-FR-15, AC-POS-12, AC-SAL-07). Contract tests run on recorded Square payloads; no network."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.config import get_settings
from app.providers import adapter_for, providers
from app.providers.base import (
    Catalog,
    Credentials,
    NormalizedOrder,
    OrderPage,
    PosAdapter,
    Registry,
    Unsupported,
)
from app.providers.square import adapter as square_adapter
from app.providers.square.adapter import SquareAdapter
from app.providers.square.normalize import to_event, to_location, to_order, to_products
from app.providers.square.webhooks import signature_for
from tests.test_square_normalize import CATALOG

D = Decimal
ORDER = {
    "id": "ord-1",
    "version": 3,
    "state": "COMPLETED",
    "location_id": "L1",
    "created_at": "2026-11-19T15:02:00Z",
    "closed_at": "2026-11-19T15:04:30Z",
    "line_items": [
        {
            "uid": "line-a",
            "catalog_object_id": "var1",
            "name": "Latte",
            "variation_name": "12 oz",
            "quantity": "2",
            "note": "oat",
            "modifiers": [{"catalog_object_id": "mod-ice", "name": "Light ice", "quantity": "1"}],
            "gross_sales_money": {"amount": 900, "currency": "USD"},
            "total_discount_money": {"amount": 100, "currency": "USD"},
        },
        {"uid": "line-b", "name": "Custom amount", "quantity": "1"},
    ],
    "returns": [
        {"uid": "ret-1", "return_line_items": [{"source_line_item_uid": "line-a", "quantity": "1"}]}
    ],
    "refunds": [{"id": "ref-1", "status": "COMPLETED", "amount_money": {"amount": 400}}],
}
CREDENTIALS = Credentials("token", "refresh", None, "merchant-1")


def test_square_is_the_first_registered_adapter_and_meets_the_contract():
    assert providers() == ("square",)
    adapter = adapter_for("square")
    assert isinstance(adapter, PosAdapter) and isinstance(adapter, SquareAdapter)
    with pytest.raises(Unsupported, match="toast"):
        adapter_for("toast")
    with pytest.raises(TypeError):
        Registry().register(object())  # type: ignore[arg-type]


def test_a_square_order_normalizes_to_lines_by_uid_with_modifiers_and_refunds():
    order = to_order(ORDER)
    assert (order.external_order_id, order.version, order.state) == ("ord-1", 3, "completed")
    assert order.occurred_at == datetime(2026, 11, 19, 15, 4, 30, tzinfo=UTC)  # completion time
    first, custom = order.lines
    assert (first.line_uid, first.external_variation_id, first.name, first.qty) == (
        "line-a",
        "var1",
        "Latte - 12 oz",
        D(2),
    )
    assert (first.gross_cents, first.net_cents, first.note) == (900, 800, "oat")
    assert first.modifiers[0].external_modifier_id == "mod-ice"
    assert custom.external_variation_id is None  # a custom amount ties to no product
    [refund] = order.refunds
    assert (refund.external_refund_id, refund.amount_cents, refund.lines[0].line_uid) == (
        "ref-1",
        400,
        "line-a",
    )


def test_the_recorded_catalog_normalizes_one_product_per_live_variation():
    products = to_products(CATALOG)
    assert products, "the recorded catalog has items"
    assert all(p.external_variation_id and p.external_item_id for p in products)
    assert len({p.external_variation_id for p in products}) == len(products)


def test_locations_and_events_normalize():
    place = to_location(
        {"id": "L1", "name": "Main", "timezone": "America/New_York", "status": "ACTIVE"}
    )
    assert (place.external_location_id, place.timezone, place.active) == (
        "L1",
        "America/New_York",
        True,
    )
    event = to_event(
        {
            "event_id": "evt-1",
            "type": "order.updated",
            "merchant_id": "merchant-1",
            "created_at": "2026-11-19T15:05:00Z",
            "data": {"id": "ord-1"},
        }
    )
    assert (event.event_id, event.entity_ids, event.external_merchant_id) == (
        "evt-1",
        ("ord-1",),
        "merchant-1",
    )


def test_a_webhook_is_verified_before_it_is_read(monkeypatch):
    monkeypatch.setattr(get_settings(), "square_webhook_signature_key", "test-key", raising=False)
    url = "https://app.test/webhooks/square"
    body = json.dumps({"event_id": "evt-2", "type": "order.created", "data": {"id": "o"}}).encode()
    good = {"X-Square-HmacSha256-Signature": signature_for(url, body, "test-key")}
    adapter = SquareAdapter()
    assert adapter.parse_webhook(body, good, url).event_id == "evt-2"
    assert adapter.parse_webhook(body, {"x-square-hmacsha256-signature": "forged"}, url) is None


def test_the_adapter_returns_normalized_types_from_the_client(monkeypatch):
    class FakeClient:
        def __init__(self, token):
            assert token == "token"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def orders(self, location_ids, since, until):
            return [ORDER]

        def catalog_objects(self, types):
            return CATALOG

        def locations(self):
            return [{"id": "L1", "name": "Main", "timezone": "UTC"}]

    monkeypatch.setattr(square_adapter, "SquareClient", FakeClient)
    adapter = SquareAdapter()
    page = adapter.fetch_orders(
        CREDENTIALS, ["L1"], datetime(2026, 11, 19, tzinfo=UTC), datetime(2026, 11, 20, tzinfo=UTC)
    )
    assert isinstance(page, OrderPage) and isinstance(page.orders[0], NormalizedOrder)
    assert isinstance(adapter.sync_catalog(CREDENTIALS), Catalog)
    assert adapter.list_locations(CREDENTIALS)[0].external_location_id == "L1"
    with pytest.raises(Unsupported):
        adapter.revoke(CREDENTIALS)


def test_ac_pos_12_another_adapter_needs_no_square_type():
    """The contract stands on its own: an adapter for any POS fills it with plain values."""

    class FakePos:
        provider = "fakepos"

        def authorize_url(self, state):
            return f"https://pos.test/auth?state={state}"

        def exchange_code(self, code):
            return CREDENTIALS

        def refresh(self, credentials):
            return credentials

        def revoke(self, credentials):
            return None

        def list_locations(self, credentials):
            return []

        def sync_catalog(self, credentials, since=None):
            return Catalog(())

        def fetch_orders(self, credentials, location_ids, since, until, cursor=None):
            return OrderPage((to_order(ORDER),))

        def parse_webhook(self, raw, headers, url):
            return None

    registry = Registry()
    registry.register(FakePos())
    assert registry.get("fakepos").fetch_orders(CREDENTIALS, [], None, None).orders[0].lines
