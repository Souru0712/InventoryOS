"""Pure mapping of raw Square payloads into normalized rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.units import Dimension

# Square measurement units -> our unit codes.
SQUARE_WEIGHT_UNITS = {
    "IMPERIAL_WEIGHT_OUNCE": "oz",
    "IMPERIAL_POUND": "lb",
    "METRIC_KILOGRAM": "kg",
    "METRIC_GRAM": "g",
    "METRIC_MILLIGRAM": "g",
}
SQUARE_VOLUME_UNITS = {
    "METRIC_MILLILITER": "ml",
    "METRIC_LITER": "l",
    "IMPERIAL_FLUID_OUNCE": "floz",
    "IMPERIAL_CUP": "cup",
    "IMPERIAL_PINT": "pt",
    "IMPERIAL_QUART": "qt",
    "IMPERIAL_GALLON": "gal",
}


def _decimal(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def normalize_locations(raw: list[dict]) -> list[dict]:
    return [
        {
            "provider_location_id": loc["id"],
            "name": loc.get("name") or loc["id"],
            "timezone": loc.get("timezone") or "UTC",
            "currency": loc.get("currency") or "USD",
            "is_active": loc.get("status", "ACTIVE") == "ACTIVE",
        }
        for loc in raw
        if loc.get("id")
    ]


def measurement_units(raw_objects: list[dict]) -> dict[str, tuple[str, Dimension]]:
    """Map MEASUREMENT_UNIT object ids to (unit_code, dimension)."""
    mapping: dict[str, tuple[str, Dimension]] = {}
    for obj in raw_objects:
        if obj.get("type") != "MEASUREMENT_UNIT":
            continue
        unit = (obj.get("measurement_unit_data") or {}).get("measurement_unit") or {}
        if code := SQUARE_WEIGHT_UNITS.get(unit.get("weight_unit", "")):
            mapping[obj["id"]] = (code, Dimension.WEIGHT)
        elif code := SQUARE_VOLUME_UNITS.get(unit.get("volume_unit", "")):
            mapping[obj["id"]] = (code, Dimension.VOLUME)
        else:
            mapping[obj["id"]] = ("each", Dimension.COUNT)
    return mapping


def normalize_catalog(raw_objects: list[dict]) -> list[dict]:
    """Flatten ITEM/ITEM_VARIATION objects into one row per sellable variation."""
    categories = {
        obj["id"]: (obj.get("category_data") or {}).get("name", "")
        for obj in raw_objects
        if obj.get("type") == "CATEGORY"
    }
    units = measurement_units(raw_objects)
    rows: list[dict] = []

    for obj in raw_objects:
        if obj.get("type") != "ITEM" or obj.get("is_deleted"):
            continue
        item_data = obj.get("item_data") or {}
        category = categories.get(item_data.get("category_id", ""), "") or None
        item_name = item_data.get("name") or obj["id"]

        for variation in item_data.get("variations") or []:
            if variation.get("is_deleted"):
                continue
            data = variation.get("item_variation_data") or {}
            price = data.get("price_money") or {}
            variation_name = data.get("name") or ""
            unit_code, _dimension = units.get(
                data.get("measurement_unit_id", ""), ("each", Dimension.COUNT)
            )
            rows.append(
                {
                    "provider_item_id": variation["id"],
                    "name": f"{item_name} - {variation_name}".strip(" -") or item_name,
                    "category": category,
                    "sku": data.get("sku"),
                    "upc": data.get("upc"),
                    "price_cents": int(price.get("amount") or 0),
                    "currency": price.get("currency") or "USD",
                    "sell_unit": unit_code,
                    "tracks_inventory": bool(data.get("track_inventory")),
                    "is_active": True,
                }
            )
    return rows


def normalize_counts(raw: list[dict]) -> list[dict]:
    """Latest IN_STOCK quantity per catalog object and location."""
    latest: dict[tuple[str, str], dict] = {}
    for count in raw:
        if count.get("state") != "IN_STOCK":
            continue
        key = (count.get("catalog_object_id", ""), count.get("location_id", ""))
        calculated_at = count.get("calculated_at") or ""
        if key in latest and latest[key]["calculated_at"] >= calculated_at:
            continue
        latest[key] = {
            "provider_item_id": key[0],
            "provider_location_id": key[1],
            "quantity": _decimal(count.get("quantity")),
            "calculated_at": calculated_at,
        }
    return list(latest.values())


def _order_day(order: dict, tz: str) -> date | None:
    stamp = order.get("closed_at") or order.get("created_at")
    if not stamp:
        return None
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    try:
        return moment.astimezone(ZoneInfo(tz)).date()
    except Exception:  # unknown tz from the POS should not lose the sale
        return moment.date()


def daily_sales_from_orders(
    raw_orders: list[dict], location_timezones: dict[str, str]
) -> list[dict]:
    """Aggregate order line items into one row per variation / location / day."""
    buckets: dict[tuple[str, str, date], dict] = defaultdict(
        lambda: {"units": Decimal("0"), "gross_cents": 0, "net_cents": 0}
    )

    for order in raw_orders:
        location_id = order.get("location_id", "")
        day = _order_day(order, location_timezones.get(location_id, "UTC"))
        if day is None:
            continue
        for line in order.get("line_items") or []:
            variation_id = line.get("catalog_object_id")
            if not variation_id:
                continue  # ad hoc / custom amounts cannot be tied to a catalog item
            gross = int(
                (line.get("gross_sales_money") or line.get("total_money") or {}).get("amount") or 0
            )
            discount = int((line.get("total_discount_money") or {}).get("amount") or 0)
            bucket = buckets[(variation_id, location_id, day)]
            bucket["units"] += _decimal(line.get("quantity"), "1")
            bucket["gross_cents"] += gross
            bucket["net_cents"] += gross - discount

    return [
        {
            "provider_item_id": variation_id,
            "provider_location_id": location_id,
            "dt": day,
            "units": values["units"],
            "gross_cents": values["gross_cents"],
            "net_cents": values["net_cents"],
        }
        for (variation_id, location_id, day), values in sorted(
            buckets.items(), key=lambda kv: kv[0][2]
        )
    ]
