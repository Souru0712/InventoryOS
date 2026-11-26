"""Drive a Square sandbox seller for the T3.11 end-to-end run (docs/roadmap/SQUARE_SANDBOX_RUN.md).

Square's test accounts start empty. This puts a café's catalog into one (sizes, modifier lists,
categories, and an item sold by the milligram to prove SQ8's unit fix), then makes the orders the
run needs: paid ones that complete, an open one, a cancelled one, and refunds.

    python scripts/seed_square_sandbox.py catalog [--dry-run]
    python scripts/seed_square_sandbox.py sell LATTE-12:2:oat LATTE-16:1   # SKU:QTY[:MODIFIER...]
    python scripts/seed_square_sandbox.py sell LATTE-12:1 --comp           # a 100 % discount
    python scripts/seed_square_sandbox.py open LATTE-12:1                  # created, never paid
    python scripts/seed_square_sandbox.py cancel LATTE-12:1                # created, then cancelled
    python scripts/seed_square_sandbox.py refund ORDER_ID --cents 250      # money back on a sale
    python scripts/seed_square_sandbox.py orders                           # the latest, by state
    python scripts/seed_square_sandbox.py location Downtown                # a second location
    python scripts/seed_square_sandbox.py --location Downtown sell LATTE-12:1

The token is the sandbox seller's (Developer Dashboard -> app -> Sandbox -> Credentials ->
"Sandbox Access token"), from SQUARE_SANDBOX_ACCESS_TOKEN in the environment or .env, or --token.
It is never printed. The script refuses to run unless SQUARE_ENV is "sandbox". Run `catalog` on an
empty seller: a second run finds the items (by SKU) and the lists (by name) there and creates
nothing.

Development tooling, not product code: nothing in app/ imports it.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEST_CARD = "cnon:card-nonce-ok"  # Square's sandbox nonce for a card that is always approved
CURRENCY = "USD"

CATEGORIES = {"coffee": "Coffee", "bakery": "Bakery", "pantry": "Pantry"}
MODIFIER_LISTS: dict[str, tuple[str, list[tuple[str, str, int]]]] = {
    # list key: (list name, [(modifier key, name, price cents)])
    "milk": ("Milk", [("oat", "Oat milk", 75), ("whole", "Whole milk", 0)]),
    "ice": ("Ice", [("lightice", "Light ice", 0)]),
}
# SKU -> (item name, variation name, price cents, category, modifier lists, measured in mg)
ITEMS: dict[str, tuple[str, str, int, str, tuple[str, ...], bool]] = {
    "LATTE-12": ("Latte", "12 oz", 450, "coffee", ("milk", "ice"), False),
    "LATTE-16": ("Latte", "16 oz", 525, "coffee", ("milk", "ice"), False),
    "BREW-16": ("Cold brew", "16 oz", 500, "coffee", ("ice",), False),
    "CROISSANT": ("Croissant", "Regular", 375, "bakery", (), False),
    "SAFFRON-MG": ("Saffron", "By the milligram", 2, "pantry", (), True),
}


def from_env_file(key: str) -> str:
    """Read one key out of .env, which pydantic loads for the app but not into os.environ."""
    path = ROOT / ".env"
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


# --- what is sent (pure, tested) -------------------------------------------------------------------


def catalog_objects(
    skip_skus: AbstractSet[str] = frozenset(), skip_lists: AbstractSet[str] = frozenset()
) -> list[dict[str, Any]]:
    """Square catalog objects for what is not there yet, with #placeholder ids."""
    objects: list[dict[str, Any]] = [
        {"type": "CATEGORY", "id": f"#cat-{key}", "category_data": {"name": name}}
        for key, name in CATEGORIES.items()
    ]
    objects.append(
        {
            "type": "MEASUREMENT_UNIT",
            "id": "#mu-mg",
            "measurement_unit_data": {
                "measurement_unit": {"weight_unit": "METRIC_MILLIGRAM"},
                "precision": 0,
            },
        }
    )
    for key, (name, modifiers) in MODIFIER_LISTS.items():
        if name in skip_lists:
            continue
        objects.append(
            {
                "type": "MODIFIER_LIST",
                "id": f"#ml-{key}",
                "modifier_list_data": {
                    "name": name,
                    "selection_type": "MULTIPLE",
                    "modifiers": [
                        {
                            "type": "MODIFIER",
                            "id": f"#mod-{mkey}",
                            "modifier_data": {
                                "name": mname,
                                "price_money": {"amount": cents, "currency": CURRENCY},
                            },
                        }
                        for mkey, mname, cents in modifiers
                    ],
                },
            }
        )
    by_item: dict[str, list[str]] = {}
    for sku, (item, *_rest) in ITEMS.items():
        if sku not in skip_skus:
            by_item.setdefault(item, []).append(sku)
    for item, skus in by_item.items():
        _name, _size, _price, category, lists, _mg = ITEMS[skus[0]]
        objects.append(
            {
                "type": "ITEM",
                "id": f"#item-{skus[0]}",
                "item_data": {
                    "name": item,
                    "reporting_category": {"id": f"#cat-{category}"},
                    "categories": [{"id": f"#cat-{category}"}],
                    "modifier_list_info": [
                        {"modifier_list_id": f"#ml-{key}", "enabled": True}
                        for key in lists
                        if MODIFIER_LISTS[key][0] not in skip_lists
                    ],
                    "variations": [_variation(sku) for sku in skus],
                },
            }
        )
    return objects


def _variation(sku: str) -> dict:
    _item, size, cents, _category, _lists, by_mg = ITEMS[sku]
    data: dict[str, Any] = {
        "name": size,
        "sku": sku,
        "pricing_type": "FIXED_PRICING",
        "price_money": {"amount": cents, "currency": CURRENCY},
    }
    if by_mg:
        data["measurement_unit_id"] = "#mu-mg"
    return {"type": "ITEM_VARIATION", "id": f"#var-{sku}", "item_variation_data": data}


def parse_lines(specs: list[str]) -> list[tuple[str, str, list[str]]]:
    """'LATTE-12:2:oat' -> (SKU, quantity, modifier keys)."""
    lines = []
    for spec in specs:
        sku, _, rest = spec.partition(":")
        quantity, _, mods = rest.partition(":")
        if sku not in ITEMS:
            raise SystemExit(f"unknown SKU {sku!r}; known: {', '.join(ITEMS)}")
        keys = [m for m in mods.split(":") if m]
        known = {mkey for _name, mods_ in MODIFIER_LISTS.values() for mkey, *_ in mods_}
        for key in keys:
            if key not in known:
                raise SystemExit(f"unknown modifier {key!r}; known: {', '.join(sorted(known))}")
        lines.append((sku, quantity or "1", keys))
    return lines


def order_body(
    location_id: str,
    lines: list[tuple[str, str, list[str]]],
    variations: dict[str, str],
    modifiers: dict[str, str],
    *,
    comp: bool = False,
) -> dict:
    """A CreateOrder request: catalog lines with their modifiers, optionally fully discounted."""
    order: dict[str, Any] = {
        "location_id": location_id,
        "line_items": [
            {
                "uid": f"line-{index}",
                "catalog_object_id": variations[sku],
                "quantity": quantity,
                "modifiers": [{"catalog_object_id": modifiers[key]} for key in keys],
                **({"applied_discounts": [{"discount_uid": "comp"}]} if comp else {}),
            }
            for index, (sku, quantity, keys) in enumerate(lines, start=1)
        ],
    }
    if comp:
        order["discounts"] = [
            {"uid": "comp", "name": "On the house", "percentage": "100", "scope": "LINE_ITEM"}
        ]
    return {"idempotency_key": str(uuid.uuid4()), "order": order}


# --- the sandbox ---------------------------------------------------------------------------------


class Square:
    """Just enough of the API to drive a sandbox, kept out of the product's own client."""

    def __init__(self, token: str, base: str, version: str):
        self._client = httpx.Client(
            base_url=base,
            headers={
                "Authorization": f"Bearer {token}",
                "Square-Version": version,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def call(self, method: str, path: str, **kwargs) -> dict:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(
                f"Square said {response.status_code} to {method} {path}:\n{response.text[:800]}"
            )
        return response.json() if response.content else {}

    def close(self) -> None:
        self._client.close()


def pick_location(locations: list[dict], wanted: str | None) -> tuple[str, str]:
    """The active location named or numbered `wanted`, else the first active one."""
    active = [row for row in locations if row.get("status", "ACTIVE") == "ACTIVE"]
    if not active:
        raise SystemExit("this sandbox account has no active location")
    if wanted:
        key = wanted.casefold()
        found = [r for r in active if key in (r["id"].casefold(), (r.get("name") or "").casefold())]
        if not found:
            names = ", ".join(r.get("name") or r["id"] for r in active)
            raise SystemExit(f"no active location {wanted!r}; there are: {names}")
        active = found
    return active[0]["id"], active[0].get("name") or active[0]["id"]


def main_location(square: Square, wanted: str | None = None) -> tuple[str, str]:
    return pick_location(square.call("GET", "/v2/locations").get("locations") or [], wanted)


def catalog_ids(square: Square) -> tuple[dict[str, str], dict[str, str], set[str]]:
    """SKU -> variation id, modifier key -> modifier id, and the modifier list names held."""
    variations: dict[str, str] = {}
    modifiers: dict[str, str] = {}
    lists: set[str] = set()
    names = {mname: mkey for _l, mods in MODIFIER_LISTS.values() for mkey, mname, _c in mods}
    cursor = None
    while True:
        params = {"types": "ITEM,MODIFIER_LIST"}
        if cursor:
            params["cursor"] = cursor
        page = square.call("GET", "/v2/catalog/list", params=params)
        for obj in page.get("objects") or []:
            if obj.get("is_deleted"):
                continue
            for variation in (obj.get("item_data") or {}).get("variations") or []:
                sku = (variation.get("item_variation_data") or {}).get("sku")
                if sku:
                    variations[sku] = variation["id"]
            data = obj.get("modifier_list_data") or {}
            if data:
                lists.add(data.get("name") or "")
                for modifier in data.get("modifiers") or []:
                    key = names.get((modifier.get("modifier_data") or {}).get("name") or "")
                    if key:
                        modifiers[key] = modifier["id"]
        cursor = page.get("cursor")
        if not cursor:
            return variations, modifiers, lists


def _create_order(square: Square, location: str, specs: list[str], comp: bool = False) -> dict:
    variations, modifiers, _lists = catalog_ids(square)
    lines = parse_lines(specs)
    missing = [sku for sku, _q, _m in lines if sku not in variations]
    if missing:
        raise SystemExit(f"not in the catalog yet: {', '.join(missing)}; run `catalog` first")
    body = order_body(location, lines, variations, modifiers, comp=comp)
    return square.call("POST", "/v2/orders", json=body)["order"]


def _set_state(square: Square, order: dict, state: str) -> dict:
    body = {
        "idempotency_key": str(uuid.uuid4()),
        "order": {"location_id": order["location_id"], "version": order["version"], "state": state},
    }
    return square.call("PUT", f"/v2/orders/{order['id']}", json=body)["order"]


def _report(order: dict) -> None:
    total = (order.get("total_money") or {}).get("amount", 0)
    print(f"order {order['id']}  state {order['state']}  version {order.get('version')}  ")
    print(f"  total {total / 100:.2f} {CURRENCY}  lines {len(order.get('line_items') or [])}")


def run(args: argparse.Namespace, square: Square) -> int:
    if args.command == "location":
        places = square.call("GET", "/v2/locations").get("locations") or []
        held = [p for p in places if (p.get("name") or "").casefold() == args.name.casefold()]
        made = (
            held[0]
            if held
            else square.call("POST", "/v2/locations", json={"location": {"name": args.name}})[
                "location"
            ]
        )
        print(f"location {made.get('name')} ({made['id']}){' already there' if held else ''}")
        return 0
    location, name = main_location(square, getattr(args, "location", None))
    if args.command == "catalog":
        variations, _modifiers, lists = catalog_ids(square)
        objects = catalog_objects(set(variations), lists)
        new = [o for o in objects if o["type"] in ("ITEM", "MODIFIER_LIST")]
        print(f"sandbox location: {name} ({location}); {len(new)} items and lists to create")
        if args.dry_run or not new:
            for obj in new:
                data = obj.get("item_data") or obj.get("modifier_list_data") or {}
                print(f"  {'would create' if args.dry_run else 'already there'}: {data['name']}")
            return 0
        square.call(
            "POST",
            "/v2/catalog/batch-upsert",
            json={"idempotency_key": str(uuid.uuid4()), "batches": [{"objects": objects}]},
        )
        print("catalog created. Next: connect the seller from the app and track this location.")
        return 0
    if args.command in ("sell", "open", "cancel"):
        order = _create_order(square, location, args.lines, comp=getattr(args, "comp", False))
        if args.command == "sell":
            total = order.get("total_money") or {"amount": 0, "currency": CURRENCY}
            if total["amount"]:
                square.call(
                    "POST",
                    "/v2/payments",
                    json={
                        "idempotency_key": str(uuid.uuid4()),
                        "source_id": TEST_CARD,
                        "amount_money": total,
                        "order_id": order["id"],
                        "location_id": location,
                        "autocomplete": True,
                    },
                )
            order = square.call("GET", f"/v2/orders/{order['id']}")["order"]
            if order["state"] == "OPEN":
                order = _set_state(square, order, "COMPLETED")
        elif args.command == "cancel":
            order = _set_state(square, order, "CANCELED")
        _report(order)
        return 0
    if args.command == "refund":
        order = square.call("GET", f"/v2/orders/{args.order_id}")["order"]
        payments = [t["payment_id"] for t in order.get("tenders") or [] if t.get("payment_id")]
        if not payments:
            raise SystemExit("that order has no payment to refund")
        refund = square.call(
            "POST",
            "/v2/refunds",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "payment_id": payments[0],
                "amount_money": {"amount": args.cents, "currency": CURRENCY},
                "reason": "T3.11 sandbox run",
            },
        )["refund"]
        print(f"refund {refund['id']}  status {refund['status']}  {args.cents / 100:.2f}")
        return 0
    found = (
        square.call(
            "POST",
            "/v2/orders/search",
            json={
                "location_ids": [location],
                "limit": 20,
                "query": {"sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"}},
            },
        ).get("orders")
        or []
    )
    for order in found:
        _report(order)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--token", help="sandbox access token; else SQUARE_SANDBOX_ACCESS_TOKEN")
    parser.add_argument("--location", help="the seller location's name or id (default: first)")
    sub = parser.add_subparsers(dest="command", required=True)
    place = sub.add_parser("location", help="add a location to the seller (once per name)")
    place.add_argument("name")
    catalog = sub.add_parser("catalog", help="create the café catalog")
    catalog.add_argument("--dry-run", action="store_true", help="say what it would create")
    for name, text in (
        ("sell", "create and pay an order (it completes)"),
        ("open", "create an order and leave it open"),
        ("cancel", "create an order, then cancel it"),
    ):
        command = sub.add_parser(name, help=text)
        command.add_argument("lines", nargs="+", help="SKU:QTY[:MODIFIER...], e.g. LATTE-12:2:oat")
        if name == "sell":
            command.add_argument("--comp", action="store_true", help="discount it 100 %%")
    refund = sub.add_parser("refund", help="refund part of a paid order")
    refund.add_argument("order_id")
    refund.add_argument("--cents", type=int, required=True)
    sub.add_parser("orders", help="list the latest orders and their states")
    args = parser.parse_args(argv)

    settings = get_settings()
    token = (
        args.token
        or os.environ.get("SQUARE_SANDBOX_ACCESS_TOKEN")
        or from_env_file("SQUARE_SANDBOX_ACCESS_TOKEN")
    )
    if not token:
        raise SystemExit(
            "No sandbox token. Developer Dashboard -> your app -> Sandbox -> Credentials ->\n"
            "'Sandbox Access token'. Put it in .env as SQUARE_SANDBOX_ACCESS_TOKEN, or --token."
        )
    if settings.square_env != "sandbox":
        raise SystemExit(f"SQUARE_ENV is {settings.square_env!r}; this only drives a sandbox")
    square = Square(token, settings.square_api_base, settings.square_api_version)
    try:
        return run(args, square)
    finally:
        square.close()


if __name__ == "__main__":
    raise SystemExit(main())
