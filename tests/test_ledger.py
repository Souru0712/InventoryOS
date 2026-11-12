"""T1.16-a: the ledger and its posting service (DR-01..07, DR-19, INV-FR-15..18, -21)."""

import ast
import random
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.access import OrgContext, UnscopedAccess, update_scoped
from app.ledger.posting import (
    SYSTEM,
    balance,
    movements_total,
    post_transaction,
    reverse_transaction,
)
from app.models import (
    InventoryMovement,
    InventoryTransaction,
    Location,
    StockBalance,
    StockItem,
    Tenant,
    TrackMode,
)
from app.transform.ledger import (
    LedgerRuleError,
    Line,
    apply,
    check_line,
    check_transaction,
    reversal_of,
)
from app.units import Dimension

D = Decimal
NOON = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)


def stock(db, location: Location, slug: str, cost: str = "0.25") -> StockItem:
    row = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=slug,
        name=slug.title(),
        dimension=Dimension.VOLUME,
        stock_unit="ml",
        track_mode=TrackMode.MANUAL,
        cost_per_base_cents=D(cost),
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def milk(db, tenant, location):
    item = stock(db, location, "milk")
    db.commit()
    return item


def post(db, org, kind, lines, key, **extra):
    return post_transaction(
        db,
        org,
        kind=kind,
        source_type="test",
        source_id=key,
        idempotency_key=key,
        lines=lines,
        occurred_at=NOON,
        **extra,
    )


def line(item: StockItem, movement_type: str, qty: str, **extra) -> Line:
    return Line(item.location_id, item.inventory_item_id, movement_type, D(qty), **extra)


# --- the rules ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("movement_type", "qty", "ok"),
    [
        ("receipt", "5", True),
        ("receipt", "-5", False),
        ("waste", "-2", True),
        ("waste", "2", False),
        ("count_variance", "-1", True),
        ("count_variance", "1", True),
        ("adjustment", "0", False),
        ("teleport", "1", False),
        ("receipt", "0.00001", False),  # more than four places (DR-10)
    ],
)
def test_a_movement_is_never_zero_and_its_sign_follows_its_type(movement_type, qty, ok):
    candidate = Line(1, 1, movement_type, D(qty))
    if ok:
        check_line(candidate)
    else:
        with pytest.raises(LedgerRuleError):
            check_line(candidate)


def test_a_transaction_holds_only_what_its_kind_allows():
    receipt = Line(1, 1, "receipt", D(1))
    assert check_transaction("receipt", [receipt], "k") == [receipt]
    with pytest.raises(LedgerRuleError, match="cannot hold"):
        check_transaction("waste", [receipt], "k")
    with pytest.raises(LedgerRuleError, match="at least one"):
        check_transaction("receipt", [], "k")
    with pytest.raises(LedgerRuleError, match="idempotency key"):
        check_transaction("receipt", [receipt], " ")
    with pytest.raises(LedgerRuleError, match="not reversed again"):
        reversal_of("reversal", D(1))
    assert apply({(1, 1): D(5)}, [receipt, Line(1, 1, "waste", D(-2))]) == {(1, 1): D(4)}


def test_the_database_refuses_a_movement_against_its_sign(db, milk):
    """DR-04 at the database level, whatever the code does."""
    org = OrgContext.system(milk.tenant_id)
    txn = post(db, org, "receipt", [line(milk, "receipt", "1000")], "r1")
    db.commit()
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO inventory_movements (tenant_id, transaction_id, location_id,"
                " inventory_item_id, allocation_mode, movement_type, qty_base,"
                " unit_cost_snapshot, occurred_at, recorded_at) VALUES (:t, :x, :l, :i,"
                " 'none', 'waste', '5.0000', '0', :at, :at)"
            ),
            {
                "t": milk.tenant_id,
                "x": txn.id,
                "l": milk.location_id,
                "i": milk.inventory_item_id,
                "at": NOON,
            },
        )
        db.flush()
    db.rollback()


# --- posting ------------------------------------------------------------------------------------


def test_a_posting_writes_its_movements_and_moves_the_balance_together(db, tenant, milk):
    org = OrgContext(tenant_id=tenant.id, user_id=None)
    receipt = post(db, org, "receipt", [line(milk, "receipt", "4000")], "receipt:1")
    post(db, org, "waste", [line(milk, "waste", "-250", reason="spilled")], "waste:1")
    db.commit()
    assert balance(db, org, milk.location_id, milk.inventory_item_id) == D("3750")
    assert movements_total(db, org, milk.location_id, milk.inventory_item_id) == D("3750")
    (first,) = receipt.movements
    assert first.unit_cost_snapshot == D("0.25")  # the item's cost at the time (DR-19)
    assert (receipt.kind, receipt.source_type, receipt.actor_user_id) == ("receipt", "test", None)
    stored = db.scalars(select(StockBalance)).one()
    assert stored.last_movement_id == max(m.id for m in db.scalars(select(InventoryMovement)))


def test_a_given_cost_is_kept_and_a_later_price_change_leaves_it(db, tenant, milk):
    org = OrgContext.system(tenant.id)
    post(db, org, "receipt", [line(milk, "receipt", "1000", unit_cost=D("0.31"))], "r")
    milk.cost_per_base_cents = D("0.50")
    db.commit()
    assert db.scalars(select(InventoryMovement)).one().unit_cost_snapshot == D("0.31")


def test_ac_inv_06_the_same_key_twice_posts_once_and_answers_the_same(db, tenant, milk):
    org = OrgContext.system(tenant.id)
    first = post(db, org, "receipt", [line(milk, "receipt", "1000")], "receipt:7")
    again = post(db, org, "receipt", [line(milk, "receipt", "1000")], "receipt:7")
    db.commit()
    assert again.id == first.id
    assert len(db.scalars(select(InventoryMovement)).all()) == 1
    assert balance(db, org, milk.location_id, milk.inventory_item_id) == D("1000")


def test_the_actor_is_the_member_unless_the_system_posts(db, tenant, milk):
    member = OrgContext(tenant_id=tenant.id, user_id=1)
    by_member = post(db, member, "receipt", [line(milk, "receipt", "1")], "a")
    by_system = post(db, member, "receipt", [line(milk, "receipt", "1")], "b", actor_user_id=SYSTEM)
    assert (by_member.actor_user_id, by_system.actor_user_id) == (1, None)


def test_nothing_is_posted_for_an_item_not_stocked_there_or_another_business(db, tenant, milk):
    org = OrgContext.system(tenant.id)
    elsewhere = Location(tenant_id=tenant.id, name="Harbor", timezone="UTC")
    db.add(elsewhere)
    other = Tenant(name="Juniper")
    db.add(other)
    db.flush()
    juniper_location = Location(tenant_id=other.id, name="Juniper Main", timezone="UTC")
    db.add(juniper_location)
    db.flush()
    theirs = stock(db, juniper_location, "milk")
    db.commit()
    not_here = Line(elsewhere.id, milk.inventory_item_id, "receipt", D(1))
    with pytest.raises(LedgerRuleError, match="not stocked"):
        post(db, org, "receipt", [not_here], "x")
    with pytest.raises(LedgerRuleError, match="not stocked"):
        post(db, org, "receipt", [line(theirs, "receipt", "1")], "y")
    with pytest.raises(LedgerRuleError, match="time zone"):
        post_transaction(
            db,
            org,
            kind="receipt",
            source_type="t",
            source_id="1",
            idempotency_key="z",
            lines=[line(milk, "receipt", "1")],
            occurred_at=datetime(2026, 1, 1),
        )
    assert db.scalars(select(InventoryTransaction)).all() == []


def test_a_location_limited_member_posts_only_at_their_locations(db, tenant, milk):
    harbor = Location(tenant_id=tenant.id, name="Harbor", timezone="UTC")
    db.add(harbor)
    db.flush()
    harbor_milk = stock(db, harbor, "milk")
    db.commit()
    limited = OrgContext(tenant_id=tenant.id, user_id=None, location_ids=frozenset({harbor.id}))
    post(db, limited, "receipt", [line(harbor_milk, "receipt", "1")], "ok")
    with pytest.raises(LedgerRuleError):
        post(db, limited, "receipt", [line(milk, "receipt", "1")], "not-theirs")


# --- reversal -----------------------------------------------------------------------------------


def test_a_reversal_undoes_a_posting_names_it_and_happens_once(db, tenant, milk):
    org = OrgContext.system(tenant.id)
    receipt = post(db, org, "receipt", [line(milk, "receipt", "1500")], "receipt:9")
    undo = reverse_transaction(db, org, receipt.id, reason="wrong delivery")
    again = reverse_transaction(db, org, receipt.id, reason="wrong delivery")
    db.commit()
    assert again.id == undo.id
    assert undo.reverses_transaction_id == receipt.id and undo.kind == "reversal"
    (back,) = undo.movements
    assert (back.movement_type, back.qty_base) == ("reversal", D("-1500"))
    assert back.reverses_movement_id == receipt.movements[0].id
    assert back.unit_cost_snapshot == receipt.movements[0].unit_cost_snapshot
    assert balance(db, org, milk.location_id, milk.inventory_item_id) == D("0")
    with pytest.raises(LedgerRuleError, match="not reversed again"):
        reverse_transaction(db, org, undo.id, reason="oops")


# --- AC-INV-04: the balance is the sum of the movements, always ---------------------------------

KINDS = [
    ("receipt", "receipt", 1),
    ("sale", "sale_consumption", -1),
    ("waste", "waste", -1),
    ("production", "production_output", 1),
    ("production", "production_input", -1),
    ("count", "count_variance", 0),
    ("adjustment", "adjustment", 0),
]


def test_ac_inv_04_after_any_sequence_the_balance_is_the_sum_of_movements(db, tenant, location):
    harbor = Location(tenant_id=tenant.id, name="Harbor", timezone="UTC")
    db.add(harbor)
    db.flush()
    items = [stock(db, place, slug) for place in (location, harbor) for slug in ("milk", "oats")]
    db.commit()
    org = OrgContext.system(tenant.id)
    rng = random.Random(16)  # noqa: S311 (seeded test data, not secrets)
    expected: dict[tuple[int, int], Decimal] = {}
    open_to_reverse: list[int] = []
    for sequence in range(1000):
        for step in range(rng.randint(1, 3)):
            if open_to_reverse and rng.random() < 0.1:
                original = open_to_reverse.pop(rng.randrange(len(open_to_reverse)))
                undo = reverse_transaction(db, org, original, reason="random")
                expected = apply(
                    expected,
                    [
                        Line(m.location_id, m.inventory_item_id, m.movement_type, D(m.qty_base))
                        for m in undo.movements
                    ],
                )
                continue
            kind, movement_type, sign = rng.choice(KINDS)
            item = rng.choice(items)
            amount = D(rng.randint(1, 50_000)) / D(10)
            qty = amount * (sign or rng.choice((1, -1)))
            lines = [Line(item.location_id, item.inventory_item_id, movement_type, qty)]
            txn = post(db, org, kind, lines, f"s{sequence}:{step}")
            open_to_reverse.append(txn.id)
            expected = apply(expected, lines)
        db.commit()
        for item in items:
            pair = (item.location_id, item.inventory_item_id)
            recorded = balance(db, org, *pair)
            assert recorded == movements_total(db, org, *pair) == expected.get(pair, D(0))


# --- AC-INV-05: no other path writes the ledger -------------------------------------------------


def test_ac_inv_05_only_the_posting_service_writes_the_ledger():
    """Movements, transactions and balances are named only by the model, the scoped layer's
    refusal and app/ledger: no route, job or loader can update or delete a posted movement."""
    allowed = {"app/models.py", "app/access/scoped.py"}
    names = {"InventoryMovement", "InventoryTransaction", "StockBalance"}
    users = set()
    for path in Path("app").rglob("*.py"):
        if path.as_posix().startswith("app/ledger/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        found |= {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        if found & names:
            users.add(path.as_posix())
    assert users <= allowed, users - allowed


def test_the_scoped_layer_refuses_to_update_posted_movements():
    for model in (InventoryMovement, InventoryTransaction):
        with pytest.raises(UnscopedAccess, match="append-only"):
            update_scoped(OrgContext.system(1), model)
