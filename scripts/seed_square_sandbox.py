"""Put a believable shop into a Square sandbox seller, once.

Square's test accounts start empty, and clicking four items into the sandbox dashboard by hand is
the kind of job this product exists to abolish. This creates the catalog and sets the counts.

    python scripts/seed_square_sandbox.py            # uses SQUARE_SANDBOX_ACCESS_TOKEN from .env
    python scripts/seed_square_sandbox.py --dry-run  # say what it would do, touch nothing

Safe to run twice: items are matched by SKU and skipped if they already exist, so a second run
reports what is already there rather than creating duplicates.

This is development tooling, not product code - nothing in app/ imports it.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


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


# A café's opening stock: things you count, in the units a till understands. The weights and
# volumes the product also tracks are entered in StockPulse, not in Square, which is the point.
ITEMS: list[dict[str, Any]] = [
    {
        "name": "Oat milk 1L carton",
        "sku": "OATM-1L",
        "upc": "0123450000029",
        "price_cents": 320,
        "category": "Dairy",
        "count": 24,
    },
    {
        "name": "House Blend beans 1kg",
        "sku": "BEAN-HB-1K",
        "upc": "0123450000012",
        "price_cents": 1800,
        "category": "Coffee",
        "count": 14,
    },
    {
        "name": "Takeaway cups 12oz",
        "sku": "CUP-12OZ",
        "upc": "0123450000036",
        "price_cents": 0,
        "category": "Packaging",
        "count": 420,
    },
    {
        "name": "Tonic water 200ml",
        "sku": "TONIC-200",
        "upc": "0123450000043",
        "price_cents": 180,
        "category": "Drinks",
        "count": 48,
    },
]


class Square:
    """Just enough of the API to seed a sandbox, kept out of the product's own client."""

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


def main_location(square: Square) -> tuple[str, str]:
    locations = square.call("GET", "/v2/locations").get("locations") or []
    active = [row for row in locations if row.get("status", "ACTIVE") == "ACTIVE"]
    if not active:
        raise SystemExit("this sandbox account has no active location")
    return active[0]["id"], active[0].get("name") or active[0]["id"]


def existing_skus(square: Square) -> dict[str, str]:
    """SKU -> variation id, for everything already in the catalog."""
    found: dict[str, str] = {}
    cursor = None
    while True:
        params = {"types": "ITEM"}
        if cursor:
            params["cursor"] = cursor
        page = square.call("GET", "/v2/catalog/list", params=params)
        for obj in page.get("objects") or []:
            for variation in (obj.get("item_data") or {}).get("variations") or []:
                sku = (variation.get("item_variation_data") or {}).get("sku")
                if sku:
                    found[sku] = variation["id"]
        cursor = page.get("cursor")
        if not cursor:
            return found


def build_item(item: dict) -> dict:
    """One ITEM with a single inventory-tracked variation, in Square's shape."""
    return {
        "type": "ITEM",
        "id": f"#item-{item['sku']}",
        "item_data": {
            "name": item["name"],
            "abbreviation": item["name"][:3].upper(),
            "variations": [
                {
                    "type": "ITEM_VARIATION",
                    "id": f"#var-{item['sku']}",
                    "item_variation_data": {
                        "name": "Regular",
                        "sku": item["sku"],
                        "upc": item["upc"],
                        "pricing_type": "FIXED_PRICING",
                        "price_money": {"amount": item["price_cents"], "currency": "USD"},
                        # Without this, Square holds no count for the item and there is nothing to sync.
                        "track_inventory": True,
                    },
                }
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--token", help="sandbox access token; defaults to SQUARE_SANDBOX_ACCESS_TOKEN"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would happen, change nothing"
    )
    args = parser.parse_args()

    settings = get_settings()
    token = (
        args.token
        or os.environ.get("SQUARE_SANDBOX_ACCESS_TOKEN")
        or from_env_file("SQUARE_SANDBOX_ACCESS_TOKEN")
    )
    if not token:
        raise SystemExit(
            "No sandbox token. Developer Dashboard -> your app -> environment toggle on Sandbox ->\n"
            "Credentials -> 'Sandbox Access token'. Put it in .env as SQUARE_SANDBOX_ACCESS_TOKEN,\n"
            "or pass --token."
        )
    if settings.square_env != "sandbox":
        raise SystemExit(
            f"SQUARE_ENV is {settings.square_env!r}; refusing to seed anything but a sandbox"
        )

    square = Square(token, settings.square_api_base, settings.square_api_version)
    try:
        location_id, location_name = main_location(square)
        print(f"sandbox location: {location_name} ({location_id})")

        known = existing_skus(square)
        wanted = [item for item in ITEMS if item["sku"] not in known]
        print(f"catalog holds {len(known)} variations; {len(wanted)} of the {len(ITEMS)} to create")

        if args.dry_run:
            for item in wanted:
                print(
                    f"  would create {item['name']} ({item['sku']}) and set {item['count']} on hand"
                )
            for item in ITEMS:
                if item["sku"] in known:
                    print(f"  already there: {item['name']} ({item['sku']})")
            return 0

        if wanted:
            created = square.call(
                "POST",
                "/v2/catalog/batch-upsert",
                json={
                    "idempotency_key": str(uuid.uuid4()),
                    "batches": [{"objects": [build_item(item) for item in wanted]}],
                },
            )
            # Square hands back its own ids against the #placeholders we sent.
            mapping = {
                row["client_object_id"].removeprefix("#"): row["object_id"]
                for row in created.get("id_mappings") or []
            }
            for item in wanted:
                known[item["sku"]] = mapping[f"var-{item['sku']}"]
                print(f"  created {item['name']} ({item['sku']})")

        changes = [
            {
                "type": "PHYSICAL_COUNT",
                "physical_count": {
                    "catalog_object_id": known[item["sku"]],
                    "location_id": location_id,
                    "state": "IN_STOCK",
                    "quantity": str(item["count"]),
                    "occurred_at": datetime.now(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z"),
                },
            }
            for item in ITEMS
        ]
        square.call(
            "POST",
            "/v2/inventory/changes/batch-create",
            json={"idempotency_key": str(uuid.uuid4()), "changes": changes},
        )
        for item in ITEMS:
            print(f"  {item['name']}: {item['count']} on hand")

        print("\nSandbox seller is ready. Next: connect it from the app, and the catalog imports.")
        return 0
    finally:
        square.close()


if __name__ == "__main__":
    raise SystemExit(main())
