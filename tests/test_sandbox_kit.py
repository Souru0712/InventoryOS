"""T3.11's kit: the sandbox script sends what the product reads back correctly, and the run report
says what arrived. No network: the script's requests are built and read, never sent."""

import importlib.util
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.config import get_settings
from app.pos_report import pos_report
from app.providers.square.normalize import category_of, measurement_units, to_modifiers, to_products
from tests.test_order_ingestion import NOW, _read, latte, order, shop  # noqa: F401

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_square_sandbox.py"
spec = importlib.util.spec_from_file_location("seed_square_sandbox", SCRIPT)
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


def as_square_returns(objects: list[dict]) -> list[dict]:
    """What Square hands back for a batch upsert: our #placeholders replaced by its ids."""
    text = json.dumps(objects)
    for placeholder in sorted(set(re.findall(r'"#([\w-]+)"', text)), key=len)[::-1]:
        text = text.replace(f'"#{placeholder}"', f'"SQ-{placeholder.upper()}"')
    return json.loads(text)


def test_the_catalog_the_run_creates_imports_with_sizes_modifiers_categories_and_units():
    raw = as_square_returns(kit.catalog_objects())
    products = {(p.name, p.variation_name): p for p in to_products(raw)}
    assert set(products) == {
        ("Latte", "12 oz"),
        ("Latte", "16 oz"),
        ("Cold brew", "16 oz"),
        ("Croissant", "Regular"),
        ("Saffron", "By the milligram"),
    }
    assert products[("Latte", "12 oz")].category == "Coffee", "read from reporting_category"
    assert products[("Saffron", "By the milligram")].unit == "mg", "SQ8 on a real catalog shape"
    assert products[("Latte", "16 oz")].price_cents == 525
    modifiers = {(m.list_name, m.name) for m in to_modifiers(raw)}
    assert modifiers == {("Milk", "Oat milk"), ("Milk", "Whole milk"), ("Ice", "Light ice")}
    assert list(measurement_units(raw).values())[0][0] == "mg"


def test_a_second_run_creates_only_what_is_missing():
    objects = kit.catalog_objects(set(kit.ITEMS) - {"CROISSANT"}, {"Milk", "Ice"})
    items = [o for o in objects if o["type"] == "ITEM"]
    assert [i["item_data"]["name"] for i in items] == ["Croissant"]
    assert not [o for o in objects if o["type"] == "MODIFIER_LIST"]


def test_orders_are_built_from_skus_and_modifier_keys():
    lines = kit.parse_lines(["LATTE-12:2:oat:lightice", "CROISSANT"])
    assert lines == [("LATTE-12", "2", ["oat", "lightice"]), ("CROISSANT", "1", [])]
    body = kit.order_body(
        "L1",
        lines,
        {"LATTE-12": "V12", "CROISSANT": "VC"},
        {"oat": "M1", "lightice": "M2"},
        comp=True,
    )["order"]
    first = body["line_items"][0]
    assert (first["catalog_object_id"], first["quantity"]) == ("V12", "2")
    assert [m["catalog_object_id"] for m in first["modifiers"]] == ["M1", "M2"]
    assert body["discounts"][0]["percentage"] == "100" and first["applied_discounts"]
    with pytest.raises(SystemExit, match="unknown SKU"):
        kit.parse_lines(["MOCHA:1"])
    with pytest.raises(SystemExit, match="unknown modifier"):
        kit.parse_lines(["LATTE-12:1:soy"])


def test_a_location_is_picked_by_name_or_id_and_only_an_active_one():
    places = [
        {"id": "L1", "name": "InventoryOS"},
        {"id": "L2", "name": "Downtown"},
        {"id": "L3", "name": "Closed", "status": "INACTIVE"},
    ]
    assert kit.pick_location(places, None) == ("L1", "InventoryOS")
    assert kit.pick_location(places, "downtown") == ("L2", "Downtown")
    assert kit.pick_location(places, "L2") == ("L2", "Downtown")
    with pytest.raises(SystemExit, match="no active location 'Closed'"):
        kit.pick_location(places, "Closed")


def test_the_script_drives_only_a_sandbox_and_never_without_a_token(monkeypatch):
    monkeypatch.delenv("SQUARE_SANDBOX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(kit, "from_env_file", lambda _key: "")
    with pytest.raises(SystemExit, match="No sandbox token"):
        kit.main(["orders"])
    monkeypatch.setattr(get_settings(), "square_env", "production", raising=False)
    with pytest.raises(SystemExit, match="only drives a sandbox"):
        kit.main(["--token", "t", "orders"])


@pytest.mark.parametrize(
    ("data", "found"),
    [
        ({"category_id": "OLD"}, "OLD"),
        ({"categories": [{"id": "C1"}, {"id": "C2"}]}, "C1"),
        ({"reporting_category": {"id": "R"}, "categories": [{"id": "C1"}]}, "R"),
        ({}, ""),
    ],
)
def test_a_category_is_read_from_whichever_field_square_sends(data, found):
    assert category_of(data) == found


def test_the_run_report_says_what_arrived(db, shop):
    org, pos, *_rest = shop
    pos.orders = [
        order("o-1", latte("A", 2)),
        order("o-2", latte("A", 1), state="open"),
        order("o-3", latte("A", 1), state="canceled", at=NOW - timedelta(minutes=1)),
    ]
    _read(db, org)
    report = "\n".join(pos_report(db, org))
    assert "connection: active, merchant M-1" in report
    assert "orders: canceled 1, completed 1, open 1; 1 became sales" in report
    assert "mappings: 1 of 1 products mapped" in report
    assert "run: " in report and " orders success" in report
    assert "money (last 7 days): sales 9.00, refunded 0.00, net 9.00" in report
