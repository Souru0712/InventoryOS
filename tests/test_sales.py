"""T2.04: the sales model and sources (POS-FR-17, -18, DR-29..31, DR-34, AC-SAL-08 origin)."""

import ast
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import OrgContext
from app.load.upsert import upsert_sales_daily
from app.models import (
    ExceptionRecord,
    SalesReturn,
    SalesTransaction,
    SoldItem,
)
from app.products import NewVariation, create_product, save_modifier
from app.sales import add_source, daily_sales, record_sale, source_of_record, stop_source
from app.transform.sales import (
    SaleLine,
    SalesRuleError,
    Span,
    check_lines,
    check_new_source,
    check_quantity,
    of_record,
)

D = Decimal
DAY = date(2026, 11, 10)


def line(key="a", qty="1", variation=None, ref=None, **extra):
    return SaleLine(key, D(qty), variation, ref, **extra)


# --- the rules (pure) --------------------------------------------------------------------------


def test_one_source_of_record_per_business_date():
    pos = Span(date(2026, 11, 1), date(2026, 11, 14))
    assert pos.covers(date(2026, 11, 14)) and not pos.covers(date(2026, 11, 15))
    with pytest.raises(SalesRuleError, match="already takes its sales"):
        check_new_source(Span(date(2026, 11, 14)), [pos])  # one shared day is one too many
    assert check_new_source(Span(date(2026, 11, 15)), [pos])  # the day after is free
    sources = [(1, pos), (2, Span(date(2026, 11, 15)))]
    assert (of_record(sources, date(2026, 11, 3)), of_record(sources, date(2026, 12, 1))) == (1, 2)
    assert of_record(sources, date(2026, 10, 31)) is None


def test_a_line_says_what_was_sold_once_and_never_less_than_nothing():
    with pytest.raises(SalesRuleError, match="appears twice"):
        check_lines([line("a", ref="x"), line("a", ref="y")])
    with pytest.raises(SalesRuleError, match="does not say what was sold"):
        check_lines([line("a")])
    with pytest.raises(SalesRuleError, match="record a return"):
        check_quantity("-1")
    assert check_quantity("2.5") == D("2.5000")


# --- AC (T2.04): one source of record; overlap raises sales_source_overlap ---------------------


def test_a_second_source_for_the_same_days_is_refused_and_its_sales_are_never_counted(
    db, tenant, location
):
    org = OrgContext.system(tenant.id)
    golden = create_product(db, org, name="Golden State", variations=[NewVariation("16 oz")])
    size = golden.variations[0].id
    pos = add_source(
        db,
        org,
        location_id=location.id,
        kind="pos_adapter",
        provider="square",
        active_from=date(2026, 11, 1),
    )
    with pytest.raises(SalesRuleError, match="already takes its sales"):
        add_source(db, org, location_id=location.id, kind="manual", active_from=DAY)
    stop_source(db, org, pos.id, date(2026, 11, 11))
    manual = add_source(
        db, org, location_id=location.id, kind="manual", active_from=date(2026, 11, 12)
    )
    assert source_of_record(db, org, location.id, DAY).id == pos.id
    assert source_of_record(db, org, location.id, date(2026, 11, 12)).id == manual.id

    counted = record_sale(
        db, org, pos, source_reference="order-1", lines=[line("1", "2", size)], day=DAY
    )
    late = record_sale(  # the POS still sends a sale after the switch to entering by hand
        db,
        org,
        pos,
        source_reference="order-2",
        lines=[line("1", "3", size)],
        day=date(2026, 11, 12),
    )
    db.commit()
    assert (counted.of_record, counted.origin, counted.lines[0].consumption_status) == (
        True,
        "pos_adapter",
        "pending",
    )
    assert (late.of_record, late.lines[0].consumption_status) == (False, "skipped")  # kept
    [overlap] = db.scalars(select(ExceptionRecord)).all()
    assert (overlap.type, overlap.subject_id) == (
        "sales_source_overlap",
        f"{location.id}:2026-11-12",
    )
    assert [(s.day, s.units) for s in daily_sales(db, org)] == [(DAY, D("2.0000"))]


def test_a_changed_resend_is_the_next_revision_and_only_the_latest_counts(db, tenant, location):
    org = OrgContext.system(tenant.id)
    golden = create_product(db, org, name="Golden State", variations=[NewVariation("16 oz")])
    size = golden.variations[0].id
    shot = save_modifier(db, org, name="Ginger shot", group="", effect="add", scale_factor="")
    pos = add_source(db, org, location_id=location.id, kind="pos_adapter", active_from=DAY)
    sale = [line("1", "2", size, modifiers=((shot.id, 2),), net_cents=1400)]
    first = record_sale(db, org, pos, source_reference="o-9", lines=sale, day=DAY, net_cents=1400)
    assert (
        record_sale(db, org, pos, source_reference="o-9", lines=sale, day=DAY, net_cents=1400)
        is None
    )  # the same again changes nothing
    second = record_sale(
        db,
        org,
        pos,
        source_reference="o-9",
        lines=[line("1", "3", size)],
        day=DAY,
        net_cents=2100,
    )
    db.commit()
    assert (first.status, second.revision, second.status) == ("corrected", 2, "completed")
    assert [(m.modifier_id, m.quantity) for m in first.lines[0].modifiers] == [(shot.id, 2)]
    assert [s.units for s in daily_sales(db, org)] == [D("3.0000")]


def test_a_sale_belongs_to_its_locations_business_day(db, tenant, location):
    location.timezone = "America/New_York"
    location.business_day_cutoff_hour = 4
    db.commit()
    org = OrgContext.system(tenant.id)
    golden = create_product(db, org, name="Golden State", variations=[NewVariation("16 oz")])
    pos = add_source(db, org, location_id=location.id, kind="pos_adapter", active_from=DAY)
    # 02:30 on the 12th in New York, before the 04:00 cutoff: the evening of the 11th.
    sale = record_sale(
        db,
        org,
        pos,
        source_reference="late",
        lines=[line("1", "1", golden.variations[0].id)],
        occurred_at=datetime(2026, 11, 12, 7, 30, tzinfo=UTC),
    )
    assert sale.business_date == date(2026, 11, 11)
    with pytest.raises(SalesRuleError, match="a time or a business date"):
        record_sale(db, org, pos, source_reference="none", lines=[line("1", ref="x")])


# --- the legacy daily feed and the freeze (DM-7) -----------------------------------------------


def test_the_legacy_square_feed_becomes_daily_transactions(db, tenant, location):
    location.provider_location_id = "SQ-LOC"
    db.add(SoldItem(tenant_id=tenant.id, provider_item_id="VAR-1", name="Latte - 12 oz"))
    db.commit()
    rows = [
        {
            "provider_location_id": "SQ-LOC",
            "provider_item_id": "VAR-1",
            "dt": DAY - timedelta(1),
            "units": D(5),
            "gross_cents": 2000,
            "net_cents": 1900,
        },
        {
            "provider_location_id": "SQ-LOC",
            "provider_item_id": "VAR-1",
            "dt": DAY,
            "units": D(7),
            "gross_cents": 2800,
            "net_cents": 2700,
        },
        {
            "provider_location_id": "SQ-ELSEWHERE",
            "provider_item_id": "VAR-1",
            "dt": DAY,
            "units": D(9),
            "gross_cents": 0,
            "net_cents": 0,
        },
    ]
    assert upsert_sales_daily(db, tenant.id, rows) == 2
    assert upsert_sales_daily(db, tenant.id, rows) == 0  # the next sync: nothing new
    rows[1]["units"] = D(8)
    assert upsert_sales_daily(db, tenant.id, rows) == 1  # the day changed: its next revision
    db.commit()
    org = OrgContext.system(tenant.id)
    sold = db.scalars(select(SoldItem)).one()
    assert sorted((s.day, s.units, s.sold_item_id) for s in daily_sales(db, org)) == [
        (DAY - timedelta(1), D("5.0000"), sold.id),
        (DAY, D("8.0000"), sold.id),
    ]
    [today] = db.scalars(
        select(SalesTransaction).where(
            SalesTransaction.status == "completed", SalesTransaction.business_date == DAY
        )
    ).all()
    assert (today.origin, today.source_reference, today.revision) == (
        "pos_adapter",
        "daily:2026-11-10",
        2,
    )
    assert (today.lines[0].external_ref, today.lines[0].description) == ("VAR-1", "Latte - 12 oz")


def test_a_return_is_one_per_source_reference(db, tenant, location):
    org = OrgContext.system(tenant.id)
    pos = add_source(db, org, location_id=location.id, kind="pos_adapter", active_from=DAY)
    for _ in range(2):
        db.add(
            SalesReturn(
                tenant_id=tenant.id,
                location_id=location.id,
                sales_source_id=pos.id,
                origin="pos_adapter",
                source_reference="refund-1",
                business_date=DAY,
            )
        )
    with pytest.raises(IntegrityError):
        db.flush()


def test_sales_are_written_in_one_place():
    """DR-30, DR-34: app/sales.py alone records sales, so every one has its origin."""
    models = {
        "SalesSource",
        "SalesTransaction",
        "SalesTransactionLine",
        "SalesLineModifier",
        "SalesReturn",
        "SalesReturnLine",
    }
    found = []
    for path in sorted(Path("app").rglob("*.py")):
        if path.as_posix() == "app/sales.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = getattr(getattr(node, "func", None), "id", "")
            if isinstance(node, ast.Call) and name in models:
                found.append(f"{path.as_posix()}:{node.lineno} {name}(")
    assert found == []
