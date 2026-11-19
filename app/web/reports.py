"""Reports (T2.16, T2.17; ANL-FR-02..08, -12..14, AC-ANL-03..08): /app/reports.

Consumption split, cost of sales, count-based usage, count variance, expiry losses, receiving
and margins over a period of each location's business days, beside links to the waste report
(ANL-FR-04) and valuation (ANL-FR-02). Every table exports to CSV from the same figures
(ANL-FR-13, AC-ANL-06). Money shows only to a role with cost access and financial reports, and
never reaches the page or a file otherwise.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.access import OrgContext, select_scoped
from app.access.permissions import VIEW_COSTS
from app.costs import value_stock
from app.db import get_db
from app.models import InventoryItem, Location, Tenant
from app.orchestration.views import tenant_currency
from app.reports import (
    consumption_report,
    cost_of_sales_report,
    count_usage_report,
    days,
    expiry_report,
    margin_report,
    receiving_report,
    variance_report,
)
from app.security import location_filter, require_permission, require_tenant
from app.transform.analytics import PARTS
from app.transform.dashboard import DEFAULT_RANGE, DateRange, RangeError, check_range
from app.transform.waste import report as waste_report
from app.units import from_base, to_base
from app.waste import logged
from app.web.templating import templates

router = APIRouter()
PART_NAMES = {
    "sales": "Sales",
    "production": "Production",
    "waste": "Waste",
    "manual": "Manual usage",
}
INTERVAL_PARTS = (
    ("received", "Received"),
    ("produced", "Produced"),
    ("used_in_production", "Used in production"),
    ("wasted", "Wasted"),
    ("adjusted", "Adjusted"),
)


def _day(text: str) -> date | None:
    if not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError as exc:
        raise RangeError("A day is a date like 2026-11-05.") from exc


def _costs(org: OrgContext) -> bool:
    return org.can(VIEW_COSTS) and org.can("reports.financial")


def _by_name(rows: dict, items: dict[int, InventoryItem]) -> list:
    """Rows keyed (location, item), in item-name order."""
    return sorted(
        rows.items(),
        key=lambda kv: (items[kv[0][1]].name.lower() if kv[0][1] in items else "", kv[0]),
    )


@router.get("/app/reports", response_class=HTMLResponse)
def reports_page(
    request: Request,
    location: int | None = Depends(location_filter),
    period: str = Query(DEFAULT_RANGE, alias="range"),
    first: str = Query("", alias="from"),
    last: str = Query("", alias="to"),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("reports.operational")),
):
    range_error = None
    try:
        chosen = check_range(period, _day(first), _day(last))
    except RangeError as exc:
        chosen, range_error = DateRange(), str(exc)
    now = datetime.now(UTC)
    costs = _costs(org)
    places = list(db.scalars(select_scoped(org, Location).order_by(Location.id)))
    items = {i.id: i for i in db.scalars(select_scoped(org, InventoryItem))}
    used = consumption_report(db, org, chosen, location_id=location, now=now)
    counted = variance_report(db, org, chosen, location_id=location, now=now)
    expired = expiry_report(db, org, chosen, location_id=location, now=now)
    margin_place, margins = margin_report(db, org, location_id=location) if costs else (None, [])
    query: dict[str, object] = {"range": chosen.choice}
    if location is not None:
        query["location"] = location
    if chosen.choice == "custom" and chosen.first and chosen.last:
        query.update({"from": chosen.first.isoformat(), "to": chosen.last.isoformat()})
    return templates.TemplateResponse(
        request,
        "app/reports.html",
        {
            "title": "Reports",
            "tenant": tenant,
            "period": chosen,
            "range_error": range_error,
            "location": location,
            "places": places,
            "names": {p.id: p.name for p in places},
            "spans": days(db, org, chosen, now, location),
            "items": items,
            "parts": PARTS,
            "part_names": PART_NAMES,
            "interval_parts": INTERVAL_PARTS,
            "consumption": used,
            "consumption_rows": _by_name(used.rows, items),
            "cost_of_sales": cost_of_sales_report(db, org, chosen, location_id=location, now=now),
            "count_usage": _by_name(
                count_usage_report(db, org, chosen, location_id=location, now=now), items
            ),
            "variance": counted,
            "variance_rows": _by_name(counted.rows, items),
            "expiry": expired,
            "lost_rows": _by_name(expired.lost, items),
            "receiving": receiving_report(db, org, chosen, location_id=location, now=now),
            "margin_place": margin_place,
            "margins": margins,
            "costs": costs,
            "currency": tenant_currency(db, org),
            "csv_query": "&".join(f"{k}={v}" for k, v in query.items()),
        },
    )


# --- CSV of every report table (ANL-FR-13, AC-ANL-06) --------------------------------------------


def _money(cents: int | Decimal | None) -> str:
    """The page's figure without the currency sign, so a spreadsheet reads it as a number."""
    return "" if cents is None else f"{Decimal(cents) / 100:.2f}"


def _qty(qty_base: Decimal, item: InventoryItem | None) -> str:
    unit = item.display_unit if item else ""
    return f"{from_base(qty_base, unit).normalize():f}" if unit else f"{qty_base.normalize():f}"


def _cell(value: object) -> str:
    """A name like '=HYPERLINK(...)' must not run as a formula when the file is opened."""
    text = "" if value is None else str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") and not _number(text) else text


def _number(text: str) -> bool:
    try:
        Decimal(text)
    except ArithmeticError:
        return False
    return True


Table = tuple[list[str], list[list[str]]]


class _Tables:
    """Each report table as header and rows, worked out only when asked for."""

    def __init__(self, db, org, period, location, now, costs, as_of):
        self.db, self.org, self.period, self.location = db, org, period, location
        self.now, self.costs, self.as_of = now, costs, as_of
        self.items = {i.id: i for i in db.scalars(select_scoped(org, InventoryItem))}
        self.places = {p.id: p.name for p in db.scalars(select_scoped(org, Location))}

    def _item(self, item_id: int) -> tuple[str, str, InventoryItem | None]:
        item = self.items.get(item_id)
        return (item.name if item else "item", item.display_unit if item else "", item)

    def consumption(self) -> Table:
        report = consumption_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Location", "Item", "Unit", *(PART_NAMES[p] for p in PARTS), "Total"]
        if self.costs:
            header += [f"{PART_NAMES[p]} cost" for p in PARTS] + ["Total cost"]
        rows = []
        for (loc, item_id), row in _by_name(report.rows, self.items):
            name, unit, item = self._item(item_id)
            line = [self.places.get(loc, ""), name, unit]
            line += [_qty(row.qty[p], item) for p in PARTS] + [_qty(row.total_qty, item)]
            if self.costs:
                line += [_money(row.cents[p]) for p in PARTS] + [_money(row.total_cents)]
            rows.append(line)
        return header, rows

    def variance(self) -> Table:
        report = variance_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Location", "Item", "Unit", "Difference"] + (["Value"] if self.costs else [])
        rows = []
        for (loc, item_id), row in _by_name(report.rows, self.items):
            name, unit, item = self._item(item_id)
            rows.append(
                [self.places.get(loc, ""), name, unit, _qty(row.qty, item)]
                + ([_money(row.cents)] if self.costs else [])
            )
        return header, rows

    def expiry(self) -> Table:
        report = expiry_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Location", "Item", "Unit", "Thrown away"] + (["Cost"] if self.costs else [])
        rows = []
        for (loc, item_id), (qty, cents) in _by_name(report.lost, self.items):
            name, unit, item = self._item(item_id)
            rows.append(
                [self.places.get(loc, ""), name, unit, _qty(qty, item)]
                + ([_money(cents)] if self.costs else [])
            )
        return header, rows

    def at_risk(self) -> Table:
        report = expiry_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Location", "Item", "Unit", "Dated", "Holds"] + (
            ["At risk"] if self.costs else []
        )
        rows = []
        for risk in report.at_risk:
            name, unit, item = self._item(risk.item_id)
            rows.append(
                [
                    self.places.get(risk.location_id, ""),
                    name,
                    unit,
                    risk.expires_on.isoformat(),
                    _qty(risk.remaining, item),
                ]
                + ([_money(risk.cents)] if self.costs else [])
            )
        return header, rows

    def count_usage(self) -> Table:
        report = count_usage_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Location", "Item", "Unit", "From", "To", "Opening count"]
        header += [label for _, label in INTERVAL_PARTS] + ["Closing count", "Used"]
        rows = []
        for (loc, item_id), intervals in _by_name(report, self.items):
            name, unit, item = self._item(item_id)
            for interval in intervals:
                rows.append(
                    [
                        self.places.get(loc, ""),
                        name,
                        unit,
                        interval.opening.day.isoformat(),
                        interval.closing.day.isoformat(),
                        _qty(interval.opening.qty_base, item),
                        *(_qty(interval.parts[key], item) for key, _ in INTERVAL_PARTS),
                        _qty(interval.closing.qty_base, item),
                        _qty(interval.used, item),
                    ]
                )
        return header, rows

    def cost_of_sales(self) -> Table:
        report = cost_of_sales_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Period", "Sale lines", "With a recipe", "Coverage %"]
        coverage = report.coverage_percent
        row = [self.period.label, str(report.lines), str(report.complete), f"{coverage or ''}"]
        if self.costs:
            header.append("Cost of sales")
            row.append(_money(report.cents))
        return header, [row]

    def discrepancies(self) -> Table:
        report = receiving_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Posted", "Location", "Supplier", "Item", "Unit", "Short", "Over", "Rejected"]
        rows = [
            [
                s.posted_at.date().isoformat(),
                self.places.get(s.location_id, ""),
                s.supplier,
                self._item(s.item_id)[0],
                s.unit,
                f"{s.found.short.normalize():f}",
                f"{s.found.over.normalize():f}",
                f"{s.found.rejected.normalize():f}",
            ]
            for s in report.shortfalls
        ]
        return header, rows

    def price_changes(self) -> Table:
        self._money_only()
        report = receiving_report(
            self.db, self.org, self.period, location_id=self.location, now=self.now
        )
        header = ["Paid on", "Supplier", "Item", "Unit", "Before", "After", "Change %"]
        rows = []
        for change in report.changes:
            name, unit, _item = self._item(change.key[1])
            per = from_base(Decimal(1), unit) if unit else Decimal(1)
            rows.append(
                [
                    change.at.date().isoformat(),
                    report.suppliers.get(change.key[0], ""),
                    name,
                    unit,
                    _money(change.before_cents / per),
                    _money(change.after_cents / per),
                    f"{change.percent}",
                ]
            )
        return header, rows

    def margins(self) -> Table:
        self._money_only()
        place, found = margin_report(self.db, self.org, location_id=self.location)
        header = ["Location", "Product", "Size", "Price", "Recipe cost", "Margin %", "Complete"]
        rows = [
            [
                place.name if place else "",
                m.product,
                m.size,
                _money(m.price_cents),
                _money(m.cost_cents),
                "" if m.percent is None else f"{m.percent}",
                "yes" if m.complete else "no",
            ]
            for m in found
        ]
        return header, rows

    def waste(self) -> Table:
        spans = days(self.db, self.org, self.period, self.now, self.location)
        first = min((s[0] for s in spans.values()), default=self.now.date())
        last = max((s[1] for s in spans.values()), default=self.now.date())
        entries = logged(self.db, self.org, since=first, until=last, location_id=self.location)
        summary = waste_report(loss for entry in entries for loss in entry.losses)
        header = ["By", "Name", "Entries"] + (["Cost"] if self.costs else [])
        rows = []
        for group, totals in (
            ("Reason", summary.by_reason),
            ("Item", summary.by_item),
            ("Product", summary.by_product),
            ("Person", summary.by_person),
        ):
            for name, total in sorted(totals.items()):
                rows.append(
                    [group, name, str(total.events)]
                    + ([_money(total.cost_cents)] if self.costs else [])
                )
        return header, rows

    def valuation(self) -> Table:
        self._money_only()
        result = value_stock(self.db, self.org, location_id=self.location, as_of_day=self.as_of)
        header = ["Location", "Item", "Unit", "Quantity", "Cost per unit", "Value"]
        rows = [
            [
                self.places.get(v.holding.location_id, ""),
                v.holding.name,
                v.holding.display_unit,
                f"{from_base(v.holding.qty_base, v.holding.display_unit).normalize():f}",
                _money(
                    Decimal(v.holding.cost_per_base_cents or 0)
                    * to_base("1", v.holding.display_unit)
                ),
                _money(v.value_cents),
            ]
            for v in result.lines
        ]
        rows += [
            [
                self.places.get(h.location_id, ""),
                h.name,
                h.display_unit,
                f"{from_base(h.qty_base, h.display_unit).normalize():f}",
                "",
                "",
            ]
            for h in result.without_cost
        ]
        return header, rows

    def _money_only(self) -> None:
        if not self.costs:
            raise HTTPException(status_code=403, detail="This table is money.")


TABLES: dict[str, Callable[[_Tables], Table]] = {
    "consumption": _Tables.consumption,
    "cost_of_sales": _Tables.cost_of_sales,
    "count_usage": _Tables.count_usage,
    "variance": _Tables.variance,
    "expiry": _Tables.expiry,
    "at_risk": _Tables.at_risk,
    "discrepancies": _Tables.discrepancies,
    "price_changes": _Tables.price_changes,
    "margins": _Tables.margins,
    "waste": _Tables.waste,
    "valuation": _Tables.valuation,
}


@router.get("/app/reports.csv")
def report_csv(
    table: str = "",
    location: int | None = Depends(location_filter),
    period: str = Query(DEFAULT_RANGE, alias="range"),
    first: str = Query("", alias="from"),
    last: str = Query("", alias="to"),
    as_of: str = "",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    org: OrgContext = Depends(require_permission("data.export")),
):
    """One report table as CSV, the same figures as its page (AC-ANL-06)."""
    build = TABLES.get(table)
    if build is None:
        raise HTTPException(status_code=404, detail="no such table")
    try:
        chosen = check_range(period, _day(first), _day(last))
        day = _day(as_of)
    except RangeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    tables = _Tables(db, org, chosen, location, datetime.now(UTC), _costs(org), day)
    header, rows = build(tables)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([[_cell(value) for value in row] for row in rows])
    stamp = datetime.now(UTC).date().isoformat()
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table}-{stamp}.csv"'},
    )
