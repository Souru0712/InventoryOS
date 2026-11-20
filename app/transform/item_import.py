"""Item import rules (T2.20-a; INV-FR-08, AC-INV-12). Pure.

A CSV of items: a header row naming the columns (in any order, under common names) and a row per
item. Each row becomes the fields a new item is made from; the item rules themselves (units,
levels, duplicate names) are checked when the row is tried, so the preview reports exactly what
importing would refuse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.transform.items import ITEM_TYPE_LABELS
from app.units import UNITS

# The columns an item file may have, each under the names people use for it.
COLUMNS: dict[str, tuple[str, ...]] = {
    "name": ("name", "item", "item name", "product", "ingredient"),
    "unit": ("unit", "counted in", "display unit", "uom"),
    "category": ("category", "group"),
    "quantity": ("quantity", "on hand", "qty", "opening quantity", "count"),
    "reorder_point": ("reorder point", "reorder", "minimum", "min"),
    "par_level": ("par", "par level", "maximum", "max"),
    "cost": ("cost", "cost per unit", "unit cost", "price"),
    "type": ("type", "item type", "kind"),
    "supplier": ("supplier", "vendor"),
}
TEMPLATE_HEADER = ("Name", "Unit", "Category", "Quantity", "Reorder point", "Par", "Cost", "Type")
_TYPES = {label.lower(): key for key, label in ITEM_TYPE_LABELS.items()} | {
    key: key for key in ITEM_TYPE_LABELS
}


class ItemImportError(ValueError):
    """A file the rules refuse as a whole; nothing is imported."""


@dataclass(frozen=True)
class ItemRow:
    """One row as the file gives it, by line number (the header is line 1)."""

    line: int
    name: str
    unit: str
    dimension: str  # from the unit; empty when the unit is not known
    item_type: str  # empty when the file names a type that is not known
    values: dict[str, str] = field(default_factory=dict)


def column_map(header: Sequence[str]) -> dict[str, int]:
    """Which column holds what; a name column is required."""
    found: dict[str, int] = {}
    for index, raw in enumerate(header):
        name = " ".join(raw.lower().replace("_", " ").split())
        for key, names in COLUMNS.items():
            if name in names and key not in found:
                found[key] = index
    if "name" not in found:
        raise ItemImportError("The file needs a column for the item name (Name).")
    return found


def item_rows(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[ItemRow]:
    """Each row's fields, trimmed; a row with no name at all is left out."""
    columns = column_map(header)
    result = []
    for offset, row in enumerate(rows):

        def cell(key: str, row: Sequence[str] = row) -> str:
            index = columns.get(key)
            return row[index].strip() if index is not None and index < len(row) else ""

        name = cell("name")
        if not name:
            continue
        unit = cell("unit").lower() or "each"
        unit = {"l": "l", "lt": "l", "litre": "l", "liter": "l", "fl oz": "floz"}.get(unit, unit)
        raw_type = cell("type").lower()
        result.append(
            ItemRow(
                line=offset + 2,
                name=name,
                unit=unit,
                dimension=UNITS[unit].dimension.value if unit in UNITS else "",
                item_type=_TYPES.get(raw_type, "") if raw_type else "raw",
                values={key: cell(key) for key in columns if key not in ("name", "unit", "type")},
            )
        )
    return result


def row_problem(row: ItemRow) -> str | None:
    """What is wrong with a row before the item rules see it, or None."""
    if not row.dimension:
        return f"{row.unit} is not a unit (try each, g, kg, ml, l, oz, lb)."
    if not row.item_type:
        return "That type is not known (raw, prepared, finished, packaging, supply, resale)."
    return None
