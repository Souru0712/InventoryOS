"""Pure mapping of raw Square payloads into normalized rows."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

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

# Square measurement units -> our unit codes. Informational (SQ-09): nothing converts with them.
# A milligram is "mg", never "g" (audit SQ8: a 1000x error).
SQUARE_WEIGHT_UNITS = {
    "IMPERIAL_WEIGHT_OUNCE": "oz",
    "IMPERIAL_POUND": "lb",
    "METRIC_KILOGRAM": "kg",
    "METRIC_GRAM": "g",
    "METRIC_MILLIGRAM": "mg",
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


def category_of(item_data: dict[str, Any]) -> str:
    """The item's category id. Square replaced `category_id` with `reporting_category` and
    `categories` (2023-12); a catalog may carry any of them."""
    reporting = (item_data.get("reporting_category") or {}).get("id")
    listed = [c.get("id") for c in item_data.get("categories") or [] if c.get("id")]
    return reporting or (listed[0] if listed else "") or item_data.get("category_id") or ""


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


# --- into the adapter contract's normalized types (T3.01, INTEGRATION_REQUIREMENTS §2) ----------

_STATES = {"COMPLETED": "completed", "OPEN": "open", "CANCELED": "canceled", "DRAFT": "open"}
# An order's refund is APPROVED once the money is given back: the Orders API never says COMPLETED
# there (only the Refunds API does), as the T3.11 sandbox run showed.
_REFUND_STATES = {
    "APPROVED": "completed",
    "COMPLETED": "completed",
    "PENDING": "pending",
    "REJECTED": "rejected",
    "FAILED": "failed",
}


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
    units = measurement_units(raw_objects)
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
                    category=categories.get(category_of(data)),
                    active=not data.get("is_archived", False),
                    price_cents=_cents(vdata.get("price_money")),
                    unit=units.get(vdata.get("measurement_unit_id", ""), ("each", Dimension.COUNT))[
                        0
                    ],
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
    source_order = raw["id"]
    for ret in raw.get("returns") or []:
        # A return names the order it gives back from; a refund on an order is that order's.
        source_order = ret.get("source_order_id") or source_order
        for item in ret.get("return_line_items") or []:
            returned[ret.get("uid") or ""].append(
                RefundLine(
                    item.get("source_line_item_uid") or "", _decimal(item.get("quantity"), "1")
                )
            )
    raw_refunds = [refund for refund in raw.get("refunds") or [] if refund.get("id")]
    # Square does not say which return a refund paid for: with one refund the returned lines are
    # its own; with several, each is recorded by its amount alone rather than guessed at.
    items = tuple(line for lines_ in returned.values() for line in lines_)
    refunds = tuple(
        NormalizedRefund(
            external_refund_id=refund["id"],
            external_order_id=source_order,
            lines=items if len(raw_refunds) == 1 else (),
            amount_cents=_cents(refund.get("amount_money")),
            status=_REFUND_STATES.get(refund.get("status") or "APPROVED", "pending"),
            occurred_at=_moment(refund.get("created_at")),
        )
        for refund in raw_refunds
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
        updated_at=_moment(raw.get("updated_at")),
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
