"""T1.18: every stock write is a ledger posting (DR-01, DR-07, DR-12, DR-25, AC-RCV-05, C-34).

These replace audit probes R1, R2, R4 and R5 with tests of the behaviour that removed them:
receipts and counts post to the ledger, the derived engine and Square's overwrites are gone,
and a delivery is received once however many requests arrive.
"""

import ast
import importlib.util
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.db import SessionLocal, engine, is_postgres
from app.extract.invoice import StubExtractor
from app.items import NewItem, create_item, stock_at
from app.ledger.posting import balance, movements_total, post_count, post_transaction
from app.load.upsert import apply_provider_counts
from app.main import app
from app.models import (
    Document,
    InventoryCount,
    InventoryEvent,
    InventoryMovement,
    InventoryTransaction,
    Location,
    RecipeComponent,
    SoldItem,
    StockBalance,
    StockItem,
    TrackMode,
)
from app.orchestration import views
from app.orchestration.alerts import evaluate_and_store
from app.orchestration.receiving import (
    AlreadyApplied,
    LineDecision,
    apply_document,
    parse_document,
    save_upload,
)
from app.orchestration.scanning import receive_scanned
from app.transform import inventory
from app.transform.ledger import Line
from app.units import Dimension
from tests.ledger_helpers import opened
from tests.sales_helpers import sold_on
from tests.sample_files import jpeg

D = Decimal
APP = Path("app")
PASSWORD = "a-good-password"  # noqa: S105 (the test owner's, from conftest)
INVOICE = {
    "supplier": "Northside",
    "invoice_number": "NS-1",
    "lines": [
        {
            "raw_text": "CUPS",
            "product_name": "Cups",
            "quantity": 24,
            "unit": "each",
            "pack_size": None,
            "unit_price_cents": 10,
            "line_total_cents": 240,
        }
    ],
}
postgres_only = pytest.mark.skipif(
    not is_postgres(),
    reason="two sessions locking rows at once is a Postgres property (ADR-0018)",
)


def stock(db, location, slug="cups", qty="10", dimension=Dimension.COUNT, unit="each", **extra):
    item = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=slug,
        name=slug.title(),
        dimension=dimension,
        stock_unit=unit,
        track_mode=extra.pop("track_mode", TrackMode.MANUAL),
        qty_base=D(qty),
        **extra,
    )
    db.add(item)
    opened(db, item)
    db.commit()
    return item


def pair(item):
    return (item.location_id, item.inventory_item_id)


def recorded(db, item) -> tuple[Decimal, Decimal, Decimal]:
    """What the screens show, the balance, and the sum of the movements: always equal."""
    db.expire_all()
    org = OrgContext.system(item.tenant_id)
    row = db.get(StockItem, item.id)
    return D(row.qty_base), balance(db, org, *pair(row)), movements_total(db, org, *pair(row))


def kinds(db) -> list[str]:
    return [
        t.kind for t in db.scalars(select(InventoryTransaction).order_by(InventoryTransaction.id))
    ]


def parsed_invoice(db, tenant, location) -> Document:
    org = OrgContext.system(tenant.id)
    document = save_upload(db, org, location.id, "delivery.jpg", jpeg("cups"), "image/jpeg")
    parse_document(db, org, document.id, StubExtractor(INVOICE))
    db.commit()
    return document


@pytest.fixture
def owner(db, tenant, location):
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": "owner@rosewood.test", "password": PASSWORD})
    yield client
    client.__exit__(None, None, None)


# --- no writer outside the posting service (static) --------------------------------------------


def _sources():
    for path in sorted(APP.rglob("*.py")):
        if "ledger" not in path.parts:
            yield path, ast.parse(path.read_text(encoding="utf-8"))


def _callee(node: ast.Call) -> str:
    func = node.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


# An alert keeps the quantity it was raised at; it is a snapshot, not stock.
SNAPSHOTS = {("app/orchestration/alerts.py", "current.qty_base")}


def test_nothing_outside_the_posting_service_assigns_a_quantity():
    found = []
    for path, tree in _sources():
        where = path.as_posix()
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AugAssign | ast.AnnAssign):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr == "qty_base":
                    if (where, ast.unparse(target)) not in SNAPSHOTS:
                        found.append(f"{where}:{node.lineno} {ast.unparse(target)} =")
            if isinstance(node, ast.Call):
                name = _callee(node)
                if name in {"StockItem", "StockBalance", "values", "update_scoped"} and any(
                    keyword.arg == "qty_base" for keyword in node.keywords
                ):
                    found.append(f"{where}:{node.lineno} {name}(qty_base=...)")
                if name == "setattr" and any(
                    isinstance(arg, ast.Constant) and arg.value == "qty_base" for arg in node.args
                ):
                    found.append(f"{where}:{node.lineno} setattr(..., 'qty_base')")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "qty_base" in node.value and "SET" in node.value.upper():
                    found.append(f"{where}:{node.lineno} SQL that sets qty_base")
    assert not found, found


def test_the_legacy_tables_are_frozen():
    """DM-7: counts and stock events before the ledger, and daily sales before the sales model
    (T2.04), stay as history; nothing adds to them."""
    frozen = {"InventoryCount", "InventoryEvent", "SalesDaily"}
    found = [
        f"{path.as_posix()}:{node.lineno} {_callee(node)}("
        for path, tree in _sources()
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _callee(node) in frozen
    ]
    assert not found, found


def test_the_derived_engine_and_recalculate_are_gone(owner):
    assert not hasattr(views, "refresh_derived_quantities")
    assert not hasattr(inventory, "derived_quantity")
    assert owner.post("/app/recalculate").status_code == 404
    assert "/app/recalculate" not in owner.get("/app").text


# --- invoice receipts (DR-25, AC-RCV-05; probe R4 inverted) ------------------------------------


def test_applying_an_invoice_posts_one_receipt(db, tenant, location):
    cups = stock(db, location)
    document = parsed_invoice(db, tenant, location)
    org = OrgContext.system(tenant.id)
    apply_document(db, org, document.id, [LineDecision(document.lines[0].id, cups.id, D(24))])
    db.commit()

    assert recorded(db, cups) == (D(34), D(34), D(34))
    receipt = db.scalars(
        select(InventoryTransaction).where(InventoryTransaction.kind == "receipt")
    ).one()
    assert (receipt.source_type, receipt.source_id) == ("document", str(document.id))
    assert receipt.idempotency_key == f"receipt:document:{document.id}"
    [movement] = receipt.movements
    assert (movement.movement_type, movement.qty_base) == ("receipt", D(24))
    assert movement.unit_cost_snapshot == D(10)  # 240 cents for 24: what this delivery cost
    assert db.scalars(select(InventoryCount)).all() == []  # DM-7: nothing new in the old table


def test_r4_two_requests_that_both_saw_it_unapplied_receive_it_once(db, tenant, location):
    """The probe's race: both requests load the document before either commits."""
    cups = stock(db, location)
    document = parsed_invoice(db, tenant, location)
    line_id, document_id = document.lines[0].id, document.id
    org = OrgContext.system(tenant.id)

    first, second = SessionLocal(), SessionLocal()
    try:
        held = [first.get(Document, document_id), second.get(Document, document_id)]
        held += [held[0].lines, held[1].lines]  # both see it parsed, not applied
        apply_document(first, org, document_id, [LineDecision(line_id, cups.id, D(24))])
        first.commit()
        with pytest.raises(AlreadyApplied):
            apply_document(second, org, document_id, [LineDecision(line_id, cups.id, D(24))])
        second.rollback()
    finally:
        first.close()
        second.close()

    assert recorded(db, cups) == (D(34), D(34), D(34))
    assert kinds(db) == ["opening", "receipt"]


@postgres_only
def test_ac_rcv_05_two_simultaneous_applies_post_one_set_of_movements(db, tenant, location):
    org = OrgContext.system(tenant.id)
    cups = stock(db, location, qty="0")
    for trial in range(10):
        invoice = {**INVOICE, "invoice_number": f"NS-{trial}"}
        document = save_upload(
            db, org, location.id, f"d{trial}.jpg", jpeg(f"cups {trial}"), "image/jpeg"
        )
        parse_document(db, org, document.id, StubExtractor(invoice))
        db.commit()
        decisions = [LineDecision(document.lines[0].id, cups.id, D(24))]
        start = threading.Barrier(2)
        outcomes: list[str] = []

        def apply(document_id=document.id, decisions=decisions, start=start, outcomes=outcomes):
            with SessionLocal() as session:
                start.wait(timeout=10)
                try:
                    apply_document(session, org, document_id, decisions)
                    session.commit()
                    outcomes.append("applied")
                except AlreadyApplied:
                    session.rollback()
                    outcomes.append("already applied")

        threads = [threading.Thread(target=apply) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert sorted(outcomes) == ["already applied", "applied"]
        assert recorded(db, cups) == (D(24 * (trial + 1)),) * 3
    assert kinds(db).count("receipt") == 10


def test_a_second_apply_through_the_browser_is_told_it_was_applied(owner, db, tenant, location):
    cups = stock(db, location)
    document = parsed_invoice(db, tenant, location)
    form = {
        f"line_{document.lines[0].id}_action": "apply",
        f"line_{document.lines[0].id}_item": str(cups.id),
        f"line_{document.lines[0].id}_qty": "24",
    }
    owner.post(f"/app/receiving/{document.id}/apply", data=form)
    again = owner.post(f"/app/receiving/{document.id}/apply", data=form, follow_redirects=False)
    assert "error=already_applied" in again.headers["location"]
    assert recorded(db, cups) == (D(34), D(34), D(34))


# --- scanned receipts --------------------------------------------------------------------------


def test_a_scanned_receipt_posts_a_receipt(db, tenant, location):
    cups = stock(db, location)
    result = receive_scanned(db, OrgContext.system(tenant.id), cups, D(12))
    db.commit()
    assert result["qty_after_base"] == D(22)
    assert recorded(db, cups) == (D(22), D(22), D(22))
    receipt = db.scalars(
        select(InventoryTransaction).where(InventoryTransaction.kind == "receipt")
    ).one()
    assert receipt.source_type == "scan"
    assert db.scalars(select(InventoryEvent)).all() == []  # DM-7


# --- counts are observations (DR-12) -----------------------------------------------------------


def test_a_count_posts_the_difference_as_a_count_variance(owner, db, location):
    cups = stock(db, location)
    page = owner.post(f"/app/items/{cups.id}/count", data={"quantity": "7"})  # back on /app
    assert recorded(db, cups) == (D(7), D(7), D(7))
    count = db.scalars(
        select(InventoryTransaction).where(InventoryTransaction.kind == "count")
    ).one()
    [movement] = count.movements
    assert (movement.movement_type, movement.qty_base) == ("count_variance", D(-3))
    assert "-3 posted as a count variance" in page.text


def test_a_count_that_matches_the_record_posts_nothing(owner, db, location):
    cups = stock(db, location)
    page = owner.post(f"/app/items/{cups.id}/count", data={"quantity": "10"})
    assert kinds(db) == ["opening"]
    assert "as recorded. Nothing to post." in page.text
    db.expire_all()
    assert db.get(StockItem, cups.id).last_counted_at is not None  # it was still counted


def test_a_scanned_count_posts_and_shows_the_variance(owner, db, location):
    cups = stock(db, location)
    body = owner.post("/app/scan/count", data={"stock_item_id": cups.id, "quantity": "12"}).json()
    assert (body["qty_text"], body["variance_text"]) == ("12", "+2")
    assert recorded(db, cups) == (D(12), D(12), D(12))


def test_a_count_is_measured_against_the_balance_when_it_posts(db, tenant, location):
    """A receipt that lands between reading the screen and saving the count is not lost."""
    cups = stock(db, location)
    org = OrgContext.system(tenant.id)
    post_transaction(
        db,
        org,
        kind="receipt",
        source_type="test",
        source_id="1",
        idempotency_key="late",
        lines=[Line(*pair(cups), "receipt", D(5))],
        occurred_at=datetime.now(UTC),
    )
    posted = post_count(
        db,
        org,
        location_id=cups.location_id,
        inventory_item_id=cups.inventory_item_id,
        counted=D(12),
        source_type="test",
        source_id="2",
        idempotency_key="count",
        occurred_at=datetime.now(UTC),
    )
    db.commit()
    assert posted.movements[0].qty_base == D(-3)  # 12 counted against 15, not against 10
    assert recorded(db, cups) == (D(12), D(12), D(12))


# --- weight and volume change only by a movement (probes R1 and R2 replaced) -------------------


def test_r1_r2_sales_never_change_stock_without_a_movement(db, tenant, location):
    """Sales after a same-day count (R1) and at another location (R2) used to move milk with no
    record. Until the consumption engine (T2.05, C-40) they move nothing: no movement, no change."""
    other = Location(tenant_id=tenant.id, name="Harbor", timezone="UTC")
    db.add(other)
    db.flush()
    milk = stock(
        db,
        location,
        "milk",
        "10000",
        Dimension.VOLUME,
        "ml",
        track_mode=TrackMode.DERIVED,
        last_counted_at=datetime(2026, 11, 4, 8, 0, tzinfo=UTC),
    )
    latte = SoldItem(tenant_id=tenant.id, provider="demo", provider_item_id="latte", name="Latte")
    db.add(latte)
    db.flush()
    db.add(
        RecipeComponent(
            tenant_id=tenant.id,
            sold_item_id=latte.id,
            stock_item_id=milk.id,
            qty_base_per_sale=D(200),
        )
    )
    for place, day in ((location, date(2026, 11, 4)), (other, date(2026, 11, 5))):
        sold_on(db, place, latte, day, 10)
    db.commit()

    evaluate_and_store(db, OrgContext.system(tenant.id))
    db.commit()
    assert recorded(db, milk) == (D(10000), D(10000), D(10000))
    assert kinds(db) == ["opening"]


# --- Square counts change nothing (C-34; probe R5 replaced) ------------------------------------


def test_r5_a_square_count_never_changes_stock_and_a_repeat_is_harmless(db, tenant, location):
    cups = stock(
        db,
        location,
        track_mode=TrackMode.PROVIDER,
        provider_variation_id="VAR1",
    )
    row = {"provider_item_id": "VAR1", "provider_location_id": "loc-1", "quantity": D(7)}
    assert apply_provider_counts(db, tenant.id, [row]) == 1
    assert apply_provider_counts(db, tenant.id, [row]) == 1
    db.commit()
    assert recorded(db, cups) == (D(10), D(10), D(10))
    assert kinds(db) == ["opening"]
    assert db.get(StockItem, cups.id).last_synced_at is not None  # heard, not applied


# --- new stock rows open through the ledger ----------------------------------------------------


def test_a_new_item_opens_with_its_quantity_through_the_ledger(db, tenant, location):
    org = OrgContext.system(tenant.id)
    item = create_item(
        db,
        org,
        NewItem(
            name="Oat milk",
            location_ids=[location.id],
            dimension="volume",
            display_unit="l",
            quantity="6",
        ),
    )
    db.commit()
    row = db.scalars(select(StockItem).where(StockItem.inventory_item_id == item.id)).one()
    assert recorded(db, row) == (D(6000), D(6000), D(6000))
    opening = db.scalars(select(InventoryTransaction)).one()
    assert (opening.kind, opening.idempotency_key) == (
        "opening",
        f"opening:{item.id}:{location.id}",
    )


def test_stocking_an_item_somewhere_new_starts_at_zero_with_nothing_posted(db, tenant, location):
    org = OrgContext.system(tenant.id)
    harbor = Location(tenant_id=tenant.id, name="Harbor", timezone="UTC")
    db.add(harbor)
    db.flush()
    item = create_item(
        db,
        org,
        NewItem(name="Oat milk", location_ids=[location.id], dimension="volume", display_unit="l"),
    )
    there = stock_at(db, org, item.id, harbor.id)
    db.commit()
    assert recorded(db, there) == (D(0), D(0), D(0))
    assert db.scalars(select(InventoryMovement)).all() == []


# --- the catch-up migration (ADR-0041's consequence) -------------------------------------------


def load_catch_up():
    path = Path("migrations/versions/ba3da8bb8b6f_ledger_catch_up.py")
    spec = importlib.util.spec_from_file_location("ledger_catch_up", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.catch_up


def test_the_catch_up_posts_what_old_writers_changed_and_opens_what_they_added(db, location):
    moved = stock(db, location, "cups", "10")
    still = stock(db, location, "lids", "4")
    # What the old writers did between the opening balances and T1.18, by hand:
    moved.qty_base = D("13.5")
    added = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug="straws",
        name="Straws",
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=D(40),
    )
    db.add(added)
    db.commit()

    with engine.begin() as connection:
        assert load_catch_up()(connection) == 2
    with engine.begin() as connection:
        assert load_catch_up()(connection) == 0  # again: nothing more

    for item in (moved, still, added):
        shown, held, total = recorded(db, item)
        assert shown == held == total
    caught = {
        t.kind: t.movements[0].qty_base
        for t in db.scalars(
            select(InventoryTransaction).where(InventoryTransaction.source_type == "migration")
        )
    }
    assert caught == {"adjustment": D("3.5"), "opening": D(40)}
    assert db.scalars(select(StockBalance)).all().__len__() == 3
