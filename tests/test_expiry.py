"""Dated lots, FEFO, and projecting what will not sell before it expires."""

from datetime import date, timedelta
from decimal import Decimal

from app.access import OrgContext
from app.transform.expiry import Batch, allocate_fefo, fefo_order, project_batches
from app.units import to_base

TODAY = date(2026, 9, 10)


def lot(id: int, qty, days: int, cost=None) -> Batch:
    return Batch(
        id=id,
        received_qty_base=Decimal(str(qty)),
        expires_on=TODAY + timedelta(days=days),
        cost_per_base_cents=Decimal(str(cost)) if cost is not None else None,
    )


def by_id(positions):
    return {p.batch.id: p for p in positions}


def test_fefo_order_is_soonest_expiry_then_earliest_receipt():
    ordered = fefo_order([lot(3, 1, 20), lot(2, 1, 5), lot(1, 1, 5)])
    assert [b.id for b in ordered] == [1, 2, 3]


def test_sales_are_assumed_to_have_drained_the_soonest_lot_first():
    # 48 Gatorade arrived in two dated lots; 18 have sold since.
    allocation, undated = allocate_fefo(Decimal("30"), [lot(1, 24, 14), lot(2, 24, 63)])
    assert [(b.id, q) for b, q in allocation] == [(1, Decimal("6")), (2, Decimal("24"))]
    assert undated == Decimal("0")


def test_a_lot_never_holds_more_than_it_arrived_with():
    allocation, undated = allocate_fefo(Decimal("64"), [lot(1, 24, 10)])
    assert allocation[0][1] == Decimal("24")
    assert undated == Decimal("40")


def test_a_count_below_zero_is_treated_as_empty():
    allocation, undated = allocate_fefo(Decimal("-5"), [lot(1, 24, 10)])
    assert allocation[0][1] == Decimal("0")
    assert undated == Decimal("0")


def test_the_red_bull_case_projects_the_cans_left_at_expiry():
    # 72 on hand, selling 4 a day, batch expires in 12 days: 48 sell, 24 do not.
    positions, _ = project_batches(Decimal("72"), [lot(1, 72, 12, cost=110)], Decimal("4"), TODAY)
    [position] = positions
    assert position.projected_leftover_base == Decimal("24")
    assert position.status == "at_risk"
    assert position.at_risk_cents == 2640


def test_a_lot_that_sells_through_in_time_carries_no_risk():
    positions, _ = project_batches(Decimal("20"), [lot(1, 20, 12)], Decimal("4"), TODAY)
    assert positions[0].projected_leftover_base == Decimal("0")
    assert positions[0].at_risk_cents == 0
    # Still the lot to sell first, because it is the soonest-dated one on the shelf.
    assert positions[0].status == "sell_first"


def test_undated_stock_is_sold_before_dated_lots():
    # 40 undated units take 10 days to sell, so the dated lot never gets a turn.
    positions, undated = project_batches(Decimal("64"), [lot(1, 24, 10)], Decimal("4"), TODAY)
    assert undated == Decimal("40")
    assert positions[0].projected_leftover_base == Decimal("24")


def test_a_lot_that_expires_early_hands_its_demand_to_the_next_one():
    # Lot 1 can only sell 2 before it expires; its 8 unsold units do not block lot 2.
    positions, _ = project_batches(
        Decimal("30"), [lot(1, 10, 2), lot(2, 20, 10)], Decimal("1"), TODAY
    )
    got = by_id(positions)
    assert got[1].projected_leftover_base == Decimal("8")
    assert got[2].projected_leftover_base == Decimal("12")


def test_expired_stock_still_on_the_shelf_is_all_at_risk():
    positions, _ = project_batches(
        Decimal("30"), [lot(1, 12, -1, cost=50), lot(2, 18, 20)], Decimal("3"), TODAY
    )
    got = by_id(positions)
    assert got[1].status == "expired"
    assert got[1].remaining_base == Decimal("12")
    assert got[1].at_risk_cents == 600
    # Expired stock cannot be sold, so it takes no demand away from the live lot.
    assert got[2].projected_leftover_base == Decimal("0")


def test_nothing_selling_means_the_whole_lot_is_at_risk():
    positions, _ = project_batches(Decimal("40"), [lot(1, 40, 45)], Decimal("0"), TODAY)
    assert positions[0].projected_leftover_base == Decimal("40")


def test_without_a_sales_signal_nothing_is_projected():
    positions, _ = project_batches(Decimal("24"), [lot(1, 24, 5, cost=80)], None, TODAY)
    [position] = positions
    assert position.projected_leftover_base is None
    # Worst case stands in for the projection, so the owner still sees a number.
    assert position.at_risk_base == Decimal("24")
    assert position.at_risk_cents == 1920


def test_cost_falls_back_to_the_item_when_the_invoice_had_no_price():
    positions, _ = project_batches(
        Decimal("10"),
        [lot(1, 10, 3)],
        Decimal("0"),
        TODAY,
        fallback_cost_per_base_cents=Decimal("25"),
    )
    assert positions[0].at_risk_cents == 250


def test_sell_first_marks_the_soonest_live_lot_only():
    positions, _ = project_batches(
        Decimal("30"), [lot(1, 10, -2), lot(2, 10, 4), lot(3, 10, 30)], Decimal("5"), TODAY
    )
    got = by_id(positions)
    assert (got[1].sell_first, got[2].sell_first, got[3].sell_first) == (False, True, False)


def test_a_lot_that_has_sold_through_is_reported_as_such():
    positions, _ = project_batches(
        Decimal("10"), [lot(1, 10, 3), lot(2, 10, 30)], Decimal("1"), TODAY
    )
    assert by_id(positions)[1].status == "sold_through"


def test_volume_works_in_base_units_like_everything_else():
    # 4 cases of 6 x 1L oat milk, 1.5 L a day, best before in 10 days.
    received = to_base(24, "l")
    positions, _ = project_batches(
        received, [lot(1, received, 10, cost="0.185")], to_base("1.5", "l"), TODAY
    )
    assert positions[0].projected_leftover_base == to_base(9, "l")
    assert positions[0].at_risk_cents == 1665


# --- through the database: Square-tracked stock, alerts, the digest -------------------------

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402

from app.models import (  # noqa: E402
    EmailLog,
    ExceptionRecord,
    Location,
    SalesDaily,
    SoldItem,
    StockBatch,
    StockItem,
    TrackMode,
)
from app.orchestration.alerts import evaluate_and_store, send_alert_digest  # noqa: E402
from app.orchestration.views import expiry_positions, velocity_map  # noqa: E402
from app.units import Dimension  # noqa: E402

NOW = datetime(2026, 9, 10, 8, 0, tzinfo=UTC)


@pytest.fixture
def red_bull(db, tenant, location):
    """A Square-tracked SKU with no recipe: the convenience-store case."""
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="red-bull",
        name="Red Bull 8.4oz",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.PROVIDER,
        provider_variation_id="VAR-RB",
        qty_base=Decimal("72"),
        cost_per_base_cents=Decimal("110"),
    )
    sold = SoldItem(
        tenant_id=tenant.id, provider_item_id="VAR-RB", name="Red Bull 8.4oz", price_cents=299
    )
    db.add_all([item, sold])
    db.flush()
    for back in range(28):
        db.add(
            SalesDaily(
                tenant_id=tenant.id,
                sold_item_id=sold.id,
                location_id=location.id,
                dt=TODAY - timedelta(days=back),
                units=Decimal("4"),
            )
        )
    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            received_qty_base=Decimal("72"),
            expires_on=TODAY + timedelta(days=12),
            cost_per_base_cents=Decimal("110"),
        )
    )
    db.commit()
    return item


def test_square_tracked_stock_gets_its_velocity_from_direct_sales(db, tenant, red_bull):
    assert velocity_map(db, OrgContext.system(tenant.id), TODAY)[red_bull.id] == Decimal("4")


def test_sales_at_another_location_do_not_count(db, tenant, location, red_bull):
    other = Location(
        tenant_id=tenant.id, provider="demo", provider_location_id="loc-2", name="Harbour"
    )
    db.add(other)
    db.flush()
    sold = db.query(SoldItem).one()
    db.add(
        SalesDaily(
            tenant_id=tenant.id,
            sold_item_id=sold.id,
            location_id=other.id,
            dt=TODAY,
            units=Decimal("280"),
        )
    )
    db.commit()
    assert velocity_map(db, OrgContext.system(tenant.id), TODAY)[red_bull.id] == Decimal("4")


def test_the_projection_runs_end_to_end(db, tenant, red_bull):
    [expiry] = expiry_positions(db, OrgContext.system(tenant.id), TODAY)
    assert expiry.positions[0].projected_leftover_base == Decimal("24")


def test_an_item_with_no_sales_signal_is_not_projected(db, tenant, location):
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="yoghurt",
        name="Yoghurt",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=Decimal("10"),
    )
    db.add(item)
    db.flush()
    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            received_qty_base=Decimal("10"),
            expires_on=TODAY + timedelta(days=20),
        )
    )
    db.commit()
    [expiry] = expiry_positions(db, OrgContext.system(tenant.id), TODAY)
    # No recipe, not POS-tracked: zero velocity would be a guess, so nothing is projected.
    assert expiry.positions[0].projected_leftover_base is None


def test_a_waste_risk_alert_opens_with_the_money_at_stake(db, tenant, red_bull):
    evaluate_and_store(db, OrgContext.system(tenant.id), NOW)
    db.commit()
    alert = db.query(ExceptionRecord).filter_by(type="waste_risk").one()
    assert alert.quantity_base == Decimal("24")
    assert alert.money_impact_cents == 2640
    assert "22 Sep" in alert.title


def test_the_digest_leads_with_the_money(db, tenant, red_bull):
    evaluate_and_store(db, OrgContext.system(tenant.id), NOW)
    db.commit()
    send_alert_digest(db, OrgContext.system(tenant.id), NOW)
    db.commit()
    subject = db.query(EmailLog).one().subject
    assert subject.startswith("$26.40 of stock at risk")


def test_the_alert_resolves_once_the_stock_can_sell_in_time(db, tenant, red_bull):
    evaluate_and_store(db, OrgContext.system(tenant.id), NOW)
    db.commit()
    red_bull.qty_base = Decimal("40")  # marked down and sold; Square sync brings the new count
    db.commit()
    evaluate_and_store(db, OrgContext.system(tenant.id), NOW)
    db.commit()
    alert = db.query(ExceptionRecord).filter_by(type="waste_risk").one()
    assert alert.resolved_at is not None
    assert (alert.status, alert.resolution) == ("resolved", "system")


def test_expired_stock_raises_its_own_alert(db, tenant, location):
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="milk",
        name="Semi-skimmed 2L",
        dimension=Dimension.VOLUME,
        stock_unit="l",
        track_mode=TrackMode.MANUAL,
        qty_base=to_base(8, "l"),
    )
    db.add(item)
    db.flush()
    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=item.id,
            received_qty_base=to_base(8, "l"),
            expires_on=TODAY - timedelta(days=2),
            cost_per_base_cents=Decimal("0.07"),
        )
    )
    db.commit()
    evaluate_and_store(db, OrgContext.system(tenant.id), NOW)
    db.commit()
    alert = db.query(ExceptionRecord).filter_by(type="batch_expired_unresolved").one()
    # 8,000 ml at 0.07 cents per ml.
    assert alert.money_impact_cents == 560
    assert "08 Sep" in alert.title


# --- coverage: is the expiry figure built on anything? ---------------------------------------

from app.orchestration.views import expiry_coverage  # noqa: E402
from app.transform.expiry import coverage  # noqa: E402
from app.transform.inventory import StockView  # noqa: E402


def view(item_id, qty, cost, tracks=None, name="Item"):
    return StockView(
        id=item_id,
        slug=f"i{item_id}",
        name=name,
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode="manual",
        qty_base=Decimal(str(qty)),
        reorder_point_base=Decimal("0"),
        cost_per_base_cents=Decimal(str(cost)),
        tracks_expiry=tracks,
    )


def test_coverage_is_measured_by_value_not_by_item_count():
    # A cheap fully-dated item should not hide an expensive undated one.
    result = coverage(
        [
            (view(1, 100, 1), Decimal("0")),  # $1.00 of stock, all dated
            (view(2, 100, 99), Decimal("100")),  # $99.00 of stock, none dated
        ]
    )
    assert result.percent == 1
    assert result.dated_items == 1
    assert result.eligible_items == 2


def test_everything_dated_is_a_hundred_percent():
    assert coverage([(view(1, 10, 50), Decimal("0"))]).percent == 100


def test_nothing_dated_is_zero():
    assert coverage([(view(1, 10, 50), Decimal("10"))]).percent == 0


def test_items_needing_dates_are_ranked_by_what_is_at_stake():
    result = coverage(
        [
            (view(1, 10, 10, name="Cheap"), Decimal("10")),
            (view(2, 10, 500, name="Dear"), Decimal("10")),
        ]
    )
    assert [row.item.name for row in result.needs_dates] == ["Dear", "Cheap"]
    assert result.needs_dates[0].undated_cents == 5000


def test_stock_with_no_cost_still_reports_coverage_by_item():
    # Falling back to counting items beats showing a meaningless 0%.
    result = coverage([(view(1, 10, 0), Decimal("0")), (view(2, 10, 0), Decimal("10"))])
    assert result.percent == 50


def test_a_partly_dated_item_counts_only_the_dated_part():
    result = coverage([(view(1, 100, 10), Decimal("25"))])
    assert result.percent == 75
    assert result.rows[0].fully_dated is False


def test_dismissed_items_leave_the_figure_entirely(db, tenant, location):
    perishable = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="milk",
        name="Milk",
        dimension=Dimension.COUNT,
        stock_unit="each",
        qty_base=Decimal("10"),
        cost_per_base_cents=Decimal("100"),
    )
    cups = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="cups",
        name="Cups",
        dimension=Dimension.COUNT,
        stock_unit="each",
        qty_base=Decimal("500"),
        cost_per_base_cents=Decimal("10"),
        tracks_expiry=False,
    )
    db.add_all([perishable, cups])
    db.commit()

    result = expiry_coverage(db, OrgContext.system(tenant.id))
    assert [row.item.name for row in result.rows] == ["Milk"]
    assert result.percent == 0

    db.add(
        StockBatch(
            tenant_id=tenant.id,
            stock_item_id=perishable.id,
            received_qty_base=Decimal("10"),
            expires_on=TODAY + timedelta(days=10),
        )
    )
    db.commit()
    assert expiry_coverage(db, OrgContext.system(tenant.id)).percent == 100
