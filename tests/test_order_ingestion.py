"""T3.05: order-line ingestion (POS-FR-05, POS-FR-07, SQ-10..13, SQ-18, PD-16, AC-POS-03/04).

The POS is a fake adapter returning normalized orders; nothing reaches the network.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import order_ingestion, pos_catalog
from app.access import OrgContext
from app.access.permissions import role_permissions
from app.db import SessionLocal
from app.items import NewItem, create_item
from app.jobs import _handlers, drain
from app.ledger.posting import balance
from app.mappings import map_modifier, map_product
from app.models import (
    ExceptionRecord,
    ExternalModifier,
    ExternalOrder,
    ExternalProduct,
    Job,
    OrderCursor,
    ProviderConnection,
    SalesTransaction,
    SoldItem,
    SyncRun,
)
from app.orchestration import scheduler, sync
from app.order_ingestion import BACKFILL_DAYS, OVERLAP, ingest_orders
from app.products import NewVariation, create_product, save_modifier
from app.providers.base import (
    AdapterError,
    Credentials,
    LineModifier,
    NormalizedOrder,
    NormalizedOrderLine,
    OrderPage,
)
from app.providers.square import adapter as square_adapter
from app.providers.square.client import SquareClient
from app.recipes import NewLine, Owner, save_recipe
from app.sales import DayOfSales, record_daily_pos_sales

D = Decimal
NOW = datetime.now(UTC).replace(microsecond=0)


class FakePos:
    """A POS adapter's order read: what it was asked, and the orders it has."""

    def __init__(self):
        self.orders: list[NormalizedOrder] = []
        self.asked: list[tuple[list[str], datetime, datetime]] = []
        self.fails: AdapterError | None = None

    def fetch_orders(self, _credentials, location_ids, since, until, cursor=None):
        self.asked.append((location_ids, since, until))
        if self.fails:
            raise self.fails
        return OrderPage(tuple(self.orders))


def order(ref, *lines, version=1, state="completed", at=NOW, place="SQ-1"):
    return NormalizedOrder(
        external_order_id=ref,
        version=version,
        state=state,
        occurred_at=at,
        external_location_id=place,
        lines=tuple(lines),
        updated_at=at,
    )


def latte(uid, qty, *modifiers):
    return NormalizedOrderLine(
        uid,
        "VAR-LATTE",
        "Latte - 12 oz",
        D(qty),
        tuple(LineModifier(m, m, D(1)) for m in modifiers),
        gross_cents=450 * int(qty),
        net_cents=450 * int(qty),
    )


@pytest.fixture
def shop(db, tenant, location, monkeypatch):
    """A business tracking Square's SQ-1, its latte and light ice mapped, milk on the shelf."""
    location.provider, location.provider_location_id = "square", "SQ-1"
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    connection = ProviderConnection(
        tenant_id=tenant.id, provider="square", merchant_id="M-1", access_token_enc="enc"
    )
    db.add(connection)
    db.flush()
    pos_latte = ExternalProduct(
        tenant_id=tenant.id,
        connection_id=connection.id,
        external_variation_id="VAR-LATTE",
        name="Latte",
        variation_name="12 oz",
    )
    pos_ice, pos_shot = (
        ExternalModifier(
            tenant_id=tenant.id,
            connection_id=connection.id,
            external_modifier_id=ref,
            list_name="Drink",
            name=name,
        )
        for ref, name in (("MOD-ICE", "Light ice"), ("MOD-SHOT", "Extra shot"))
    )
    db.add_all([pos_latte, pos_ice, pos_shot])
    milk = create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            cost_per_unit="0.01",
        ),
    )
    drink = create_product(db, org, name="Latte", variations=[NewVariation("12 oz")])
    size = drink.variations[0]
    save_recipe(
        db, org, Owner("variation", size.id), [NewLine(milk.id, "200", "ml")], from_the_start=True
    )
    ice = save_modifier(db, org, name="Light ice", group="", effect="scale", scale_factor="1.15")
    db.flush()
    map_product(db, org, pos_latte.id, size.id)
    map_modifier(db, org, pos_ice.id, ice.id)
    db.commit()
    pos = FakePos()
    monkeypatch.setattr(order_ingestion, "adapter_for", lambda _provider: pos)
    monkeypatch.setattr(order_ingestion, "credentials", lambda *_args, **_kw: None)
    return org, pos, connection, milk, pos_shot


def _jobs():
    return drain(handlers={**_handlers(), "send_alert_digest": lambda *_: None})


def _read(db, org, now=NOW):
    stats = ingest_orders(db, org, now)
    db.commit()
    _jobs()
    db.expire_all()
    return stats


def _sales(db, reference):
    return list(
        db.scalars(
            select(SalesTransaction)
            .where(SalesTransaction.source_reference == reference)
            .order_by(SalesTransaction.revision)
        )
    )


def test_a_completed_order_is_one_sale_a_line_per_order_line_used_once(db, location, shop):
    org, pos, _connection, milk, _shot = shop
    custom = NormalizedOrderLine("B", None, "Custom amount", D(1), gross_cents=500)
    pos.orders = [order("o-1", latte("A", 2, "MOD-ICE"), custom, latte("C", 1))]
    assert _read(db, org)["sales"] == 1

    [sale] = _sales(db, "order:o-1")
    assert (sale.occurred_at, sale.status, sale.revision) == (NOW, "completed", 1)
    lines = {ln.line_key: ln for ln in sale.lines}
    assert set(lines) == {"line:A", "line:C"}, "a custom amount is no sale line (SQ-18)"
    assert [m.quantity for m in lines["line:A"].modifiers] == [1]
    assert balance(db, org, location.id, milk.id) == D(-660)  # 2 x 200 x 1.15 + 200
    staged = db.scalars(select(ExternalOrder)).one()
    assert (staged.version, len(staged.lines), staged.sales_transaction_id) == (1, 3, sale.id)

    # AC-POS-03: delivered again, it changes nothing.
    assert _read(db, org, NOW + timedelta(minutes=5))["unchanged"] == 1
    assert len(_sales(db, "order:o-1")) == 1
    assert balance(db, org, location.id, milk.id) == D(-660)

    # A new version is the sale's next revision: what it took is given back and taken anew.
    pos.orders = [order("o-1", latte("A", 1, "MOD-ICE"), version=2)]
    assert _read(db, org, NOW + timedelta(minutes=10))["sales"] == 1
    assert [s.status for s in _sales(db, "order:o-1")] == ["corrected", "completed"]
    assert balance(db, org, location.id, milk.id) == D(-230)
    pos.orders = [order("o-1", latte("A", 2, "MOD-ICE"), latte("C", 1))]  # the old version again
    assert _read(db, org, NOW + timedelta(minutes=15))["unchanged"] == 1
    assert balance(db, org, location.id, milk.id) == D(-230)


def test_open_cancelled_untracked_and_daily_total_days_are_staged_never_sold(db, shop):
    org, pos, _connection, _milk, _shot = shop
    pos.orders = [
        order("open", latte("A", 1), state="open"),
        order("gone", latte("A", 1), state="canceled"),
        order("elsewhere", latte("A", 1), place="SQ-OTHER"),
        order("old", latte("A", 1), at=NOW - timedelta(days=BACKFILL_DAYS + 1)),
    ]
    stats = _read(db, org)
    assert stats["staged"] == 4 and "sales" not in stats
    assert not db.scalars(select(SalesTransaction)).all()
    assert len(db.scalars(select(ExternalOrder)).all()) == 4


def test_the_cursor_starts_seven_days_back_then_follows_the_last_read_less_an_overlap(
    db, location, shop
):
    org, pos, connection, _milk, _shot = shop
    _read(db, org)
    cursor = db.scalars(select(OrderCursor)).one()
    assert cursor.lines_from == NOW.date() - timedelta(days=BACKFILL_DAYS)
    [(where, since, until)] = pos.asked
    assert (where, since, until) == (["SQ-1"], NOW - timedelta(days=9) - OVERLAP, NOW)
    later = NOW + timedelta(minutes=5)
    _read(db, org, later)
    assert pos.asked[1][1:] == (NOW - OVERLAP, later)
    runs = db.scalars(select(SyncRun).where(SyncRun.kind == "orders")).all()
    assert [r.status.value for r in runs] == ["success", "success"]


def test_the_first_read_voids_the_daily_totals_its_order_lines_replace(db, tenant, location, shop):
    """The same orders, counted once: daily totals from the first order-line day on are voided,
    older ones stay."""
    org, pos, _connection, milk, _shot = shop
    sold = SoldItem(tenant_id=tenant.id, provider="square", provider_item_id="VAR-LATTE", name="L")
    db.add(sold)
    db.flush()
    old_day = NOW.date() - timedelta(days=BACKFILL_DAYS + 3)
    record_daily_pos_sales(
        db,
        org,
        "square",
        [
            DayOfSales(location.id, NOW.date(), sold, D(3)),
            DayOfSales(location.id, old_day, sold, D(3)),
        ],
    )
    db.commit()
    _jobs()
    db.expire_all()
    assert balance(db, org, location.id, milk.id) == D(-1200)

    pos.orders = [order("o-1", latte("A", 3))]
    _read(db, org)
    assert _sales(db, f"daily:{NOW.date().isoformat()}")[-1].status == "voided"
    assert _sales(db, f"daily:{old_day.isoformat()}")[-1].status == "completed"
    assert balance(db, org, location.id, milk.id) == D(-1200)


def test_an_unmapped_modifier_is_an_exception_until_it_is_mapped(db, shop):
    org, pos, _connection, _milk, shot = shop
    pos.orders = [order("o-1", latte("A", 1, "MOD-SHOT"))]
    _read(db, org)
    problem = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.subject_type == "pos_modifier")
    ).one()
    assert (problem.subject_id, problem.status) == ("MOD-SHOT", "open")
    extra = save_modifier(db, org, name="Extra shot", group="", effect="none", scale_factor="")
    map_modifier(db, org, shot.id, extra.id)
    db.commit()
    db.refresh(problem)
    assert problem.status == "resolved"


def test_a_revoked_authorization_stops_the_read_and_a_passing_failure_retries(db, shop):
    """T3.08: a passing failure is kept (sync run, exception) and the next read tries again."""
    org, pos, connection, _milk, _shot = shop
    pos.fails = AdapterError("square refused", revoked=False)
    assert ingest_orders(db, org, NOW)["failed"] == 1
    db.commit()
    cursor = db.scalars(select(OrderCursor)).one()
    assert cursor.through < NOW, "the cursor stays where it was: nothing is skipped"
    pos.fails = AdapterError("unauthorized", revoked=True)
    assert ingest_orders(db, org, NOW) == {"revoked": 1}
    db.commit()
    db.refresh(connection)
    assert connection.status == "revoked"


# --- the legacy path and the schedule ------------------------------------------------------------


def test_the_hourly_sync_reads_no_orders(db, tenant, shop, monkeypatch):
    class Client:
        def __init__(self, _token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def locations(self):
            return []

        def catalog_objects(self, _types):
            return []

        def orders(self, *_args):
            raise AssertionError("order lines own the sales (T3.05)")

    monkeypatch.setattr(square_adapter, "SquareClient", Client)
    monkeypatch.setattr(
        pos_catalog, "credentials", lambda *_a, **_k: Credentials("token", None, None, "M-1")
    )
    assert "sales_rows" not in sync.run_sync(db, OrgContext.system(tenant.id))


def test_square_is_searched_by_update_time_in_every_state_ten_locations_at_a_time(monkeypatch):
    bodies: list[dict] = []

    def page(self, method, path, body, key):
        bodies.append(body)
        return iter([{"id": f"o-{len(bodies)}"}])

    monkeypatch.setattr(SquareClient, "_paginate", page)
    found = SquareClient("token").orders([f"L{i}" for i in range(12)], NOW, NOW)
    assert [len(b["location_ids"]) for b in bodies] == [10, 2] and len(found) == 2
    query = bodies[0]["query"]
    assert "updated_at" in query["filter"]["date_time_filter"]
    assert query["filter"]["state_filter"]["states"] == ["OPEN", "COMPLETED", "CANCELED"]
    assert query["sort"]["sort_field"] == "UPDATED_AT"


def test_orders_are_read_on_a_clock_as_jobs(db, shop):
    class Recorder:
        ids: list[str] = []

        def add_job(self, func, *args, id, **kwargs):
            self.ids.append(id)

    scheduler._add_jobs(Recorder())
    assert "queue_order_ingestion" in Recorder.ids
    scheduler.queue_order_ingestion()
    with SessionLocal() as other:
        assert other.scalars(select(Job).where(Job.kind == "ingest_orders")).one()


def test_the_cli_sync_has_no_days_window():
    from app.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["sync", "--tenant", "1", "--days", "60"])
