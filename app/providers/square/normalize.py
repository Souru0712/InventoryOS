"""Pure mapping of raw Square payloads into normalized rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.providers.base import (
    LineModifier,
    NormalizedEvent,
    NormalizedLocation,
    NormalizedModifier,
    NormalizedOrder,
    NormalizedOrderLine,
    NormalizedProduct,
    NormalizedRefund,
    RefundLine,
)
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


def normalize_locations(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def measurement_units(raw_objects: list[dict[str, Any]]) -> dict[str, tuple[str, Dimension]]:
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


def normalize_catalog(raw_objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten ITEM/ITEM_VARIATION objects into one row per sellable variation."""
    categories = {
        obj["id"]: (obj.get("category_data") or {}).get("name", "")
        for obj in raw_objects
        if obj.get("type") == "CATEGORY"
    }
    units = measurement_units(raw_objects)
    rows: list[dict[str, Any]] = []

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


def normalize_counts(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Latest IN_STOCK quantity per catalog object and location."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
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


def _order_day(order: dict[str, Any], tz: str) -> date | None:
    stamp = order.get("closed_at") or order.get("created_at")
    if not stamp:
        return None
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    try:
        return moment.astimezone(ZoneInfo(tz)).date()
    except Exception:  # unknown tz from the POS should not lose the sale
        return moment.date()


def daily_sales_from_orders(
    raw_orders: list[dict[str, Any]], location_timezones: dict[str, str]
) -> list[dict[str, Any]]:
    """Aggregate order line items into one row per variation / location / day."""
    buckets: dict[tuple[str, str, date], dict[str, Any]] = defaultdict(
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


# --- into the adapter contract's normalized types (T3.01, INTEGRATION_REQUIREMENTS §2) ----------

_STATES = {"COMPLETED": "completed", "OPEN": "open", "CANCELED": "canceled", "DRAFT": "open"}


def _moment(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _cents(money: dict[str, Any] | None) -> int | None:
    if not money or money.get("amount") is None:
        return None
    return int(money["amount"])


def to_location(raw: dict[str, Any]) -> NormalizedLocation:
    address = raw.get("address") or {}
    line = ", ".join(
        part
        for part in (
            address.get("address_line_1"),
            address.get("locality"),
            address.get("postal_code"),
        )
        if part
    )
    return NormalizedLocation(
        external_location_id=raw["id"],
        name=raw.get("name") or raw["id"],
        timezone=raw.get("timezone") or "UTC",
        address=line or None,
        active=raw.get("status", "ACTIVE") == "ACTIVE",
    )


def to_products(raw_objects: list[dict[str, Any]]) -> tuple[NormalizedProduct, ...]:
    """One product per live variation of each live item: the mapping grain."""
    categories = {
        obj["id"]: (obj.get("category_data") or {}).get("name") or None
        for obj in raw_objects
        if obj.get("type") == "CATEGORY"
    }
    products = []
    for obj in raw_objects:
        if obj.get("type") != "ITEM" or obj.get("is_deleted"):
            continue
        data = obj.get("item_data") or {}
        for variation in data.get("variations") or []:
            if variation.get("is_deleted"):
                continue
            vdata = variation.get("item_variation_data") or {}
            products.append(
                NormalizedProduct(
                    external_item_id=obj["id"],
                    external_variation_id=variation["id"],
                    name=data.get("name") or obj["id"],
                    variation_name=vdata.get("name") or "",
                    category=categories.get(data.get("category_id", "")),
                    active=not data.get("is_archived", False),
                    price_cents=_cents(vdata.get("price_money")),
                )
            )
    return tuple(products)


def to_modifiers(raw_objects: list[dict[str, Any]]) -> tuple[NormalizedModifier, ...]:
    modifiers = []
    for obj in raw_objects:
        if obj.get("type") != "MODIFIER_LIST" or obj.get("is_deleted"):
            continue
        data = obj.get("modifier_list_data") or {}
        for modifier in data.get("modifiers") or []:
            if modifier.get("is_deleted"):
                continue
            modifiers.append(
                NormalizedModifier(
                    external_modifier_id=modifier["id"],
                    list_name=data.get("name") or "",
                    name=(modifier.get("modifier_data") or {}).get("name") or modifier["id"],
                )
            )
    return tuple(modifiers)


def to_order(raw: dict[str, Any]) -> NormalizedOrder:
    """An order as Square last told it: completion time, its lines by uid, its refunds."""
    lines = []
    for line in raw.get("line_items") or []:
        gross = _cents(line.get("gross_sales_money") or line.get("total_money"))
        discount = _cents(line.get("total_discount_money")) or 0
        name = " - ".join(p for p in (line.get("name"), line.get("variation_name")) if p)
        lines.append(
            NormalizedOrderLine(
                line_uid=line.get("uid") or "",
                external_variation_id=line.get("catalog_object_id"),
                name=name or "item",
                qty=_decimal(line.get("quantity"), "1"),
                modifiers=tuple(
                    LineModifier(
                        external_modifier_id=m.get("catalog_object_id"),
                        name=m.get("name") or "",
                        qty=_decimal(m.get("quantity"), "1"),
                    )
                    for m in line.get("modifiers") or []
                ),
                note=line.get("note") or None,
                gross_cents=gross,
                net_cents=None if gross is None else gross - discount,
            )
        )
    returned: dict[str, list[RefundLine]] = defaultdict(list)
    for ret in raw.get("returns") or []:
        for item in ret.get("return_line_items") or []:
            returned[ret.get("uid") or ""].append(
                RefundLine(
                    item.get("source_line_item_uid") or "", _decimal(item.get("quantity"), "1")
                )
            )
    refunds = tuple(
        NormalizedRefund(
            external_refund_id=refund["id"],
            external_order_id=raw["id"],
            lines=tuple(line for lines_ in returned.values() for line in lines_),
            amount_cents=_cents(refund.get("amount_money")),
            status=(refund.get("status") or "COMPLETED").lower(),
        )
        for refund in raw.get("refunds") or []
        if refund.get("id")
    )
    occurred = _moment(raw.get("closed_at")) or _moment(raw.get("created_at"))
    if occurred is None:
        raise ValueError(f"order {raw.get('id')} has no time")
    return NormalizedOrder(
        external_order_id=raw["id"],
        version=int(raw.get("version") or 1),
        state=_STATES.get(raw.get("state") or "", "open"),
        occurred_at=occurred,
        external_location_id=raw.get("location_id") or "",
        lines=tuple(lines),
        refunds=refunds,
    )


def to_event(raw: dict[str, Any]) -> NormalizedEvent:
    data = raw.get("data") or {}
    return NormalizedEvent(
        event_id=raw.get("event_id") or "",
        type=raw.get("type") or "",
        entity_ids=tuple(i for i in (data.get("id"),) if i),
        occurred_at=_moment(raw.get("created_at")),
        external_merchant_id=raw.get("merchant_id") or None,
    )
