"""Sales file import rules (T2.13; STD-FR-03, AC-SAL-04, ADR-0064). Pure.

A sales export is a table: a header row and rows. A mapping says which columns hold the date,
the product and the quantity sold; rows are summed per business date and product name. A name is
matched to a product size by what the business taught before, else by the size's own label.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%Y/%m/%d", "%d-%m-%Y")
MAX_ROWS = 20000


class ImportRuleError(ValueError):
    """A file or mapping the rules refuse; nothing is written."""


def read_csv(content: bytes) -> tuple[list[str], list[list[str]]]:
    """The header and rows of a CSV export, in UTF-8 (a byte-order mark allowed) or Latin-1."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]
    if not rows:
        raise ImportRuleError("The file is empty.")
    if len(rows) > MAX_ROWS + 1:
        raise ImportRuleError(f"Import at most {MAX_ROWS} rows at a time.")
    header = [h.strip() for h in rows[0]]
    return header, rows[1:]


@dataclass(frozen=True)
class ColumnMap:
    """Which column holds what (by header name)."""

    date_column: str
    product_column: str
    quantity_column: str
    date_format: str = "auto"


def parse_date(raw: str, fmt: str = "auto") -> date:
    text = raw.strip()[:19]
    formats = DATE_FORMATS if fmt == "auto" else (fmt,)
    for candidate in formats:
        try:
            return datetime.strptime(
                text[: len(datetime.now().strftime(candidate))], candidate
            ).date()
        except ValueError:
            continue
    raise ImportRuleError(f"{raw!r} is not a date this import reads.")


def normalized(name: str) -> str:
    return " ".join(name.casefold().split())


def sold_by_day(
    header: list[str], rows: Iterable[list[str]], mapping: ColumnMap
) -> dict[tuple[date, str], Decimal]:
    """Quantity sold per (business date, product name) (STD-FR-03)."""
    try:
        at = header.index(mapping.date_column)
        what = header.index(mapping.product_column)
        how_many = header.index(mapping.quantity_column)
    except ValueError as exc:
        raise ImportRuleError("Choose a date, a product and a quantity column.") from exc
    totals: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    for number, row in enumerate(rows, start=2):
        if len(row) <= max(at, what, how_many):
            continue
        name = row[what].strip()
        if not name:
            continue
        try:
            qty = Decimal(row[how_many].strip().replace(",", "") or "0")
        except InvalidOperation as exc:
            raise ImportRuleError(f"Row {number}: {row[how_many]!r} is not a quantity.") from exc
        totals[(parse_date(row[at], mapping.date_format), name)] += qty
    return dict(totals)


def match_names(
    names: Iterable[str], learned: Mapping[str, int], labels: Mapping[str, int]
) -> tuple[dict[str, int], list[str]]:
    """Each name's product size: what was taught first, else the size's label (product and
    size, or the product alone when it has one size). The names left unmatched, sorted."""
    matched: dict[str, int] = {}
    unmatched = []
    for name in sorted(set(names)):
        key = normalized(name)
        found = learned.get(key, labels.get(key))
        if found is None:
            unmatched.append(name)
        else:
            matched[name] = found
    return matched, unmatched


def guess_columns(header: list[str]) -> ColumnMap | None:
    """A first guess from common header names, for a file seen for the first time."""
    lowered = {h.casefold(): h for h in header}

    def pick(*words: str) -> str | None:
        for word in words:
            for low, original in lowered.items():
                if word in low:
                    return original
        return None

    found = (
        pick("date", "day"),
        pick("item", "product", "name"),
        pick("qty", "quantity", "sold", "count"),
    )
    if None in found:
        return None
    day, product, qty = found
    return ColumnMap(str(day), str(product), str(qty))
