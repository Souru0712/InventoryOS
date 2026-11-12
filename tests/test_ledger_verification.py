"""T1.21: nightly ledger verification (DR-03, REL-08, AC-INV-11, OBS-06)."""

import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, update

from app.access import OrgContext
from app.jobs import drain
from app.ledger.posting import post_transaction
from app.models import (
    ExceptionRecord,
    InventoryMovement,
    Job,
    Location,
    StockBalance,
    StockItem,
    Tenant,
    TrackMode,
)
from app.orchestration import scheduler
from app.transform.ledger import Drift, Line, find_drift
from app.units import Dimension
from tests.ledger_helpers import opened

D = Decimal
NIGHT = datetime(2026, 11, 13, 2, 45, tzinfo=UTC)


def stock(db, location, slug="oat-milk", qty="12"):
    item = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=slug,
        name=slug.replace("-", " ").title(),
        dimension=Dimension.VOLUME,
        stock_unit="l",
        track_mode=TrackMode.MANUAL,
        qty_base=D(qty),
    )
    db.add(item)
    opened(db, item)
    db.commit()
    return item


def receive(db, item, qty, key):
    post_transaction(
        db,
        OrgContext.system(item.tenant_id),
        kind="receipt",
        source_type="test",
        source_id=key,
        idempotency_key=key,
        lines=[Line(item.location_id, item.inventory_item_id, "receipt", D(qty))],
        occurred_at=datetime.now(UTC),
    )
    db.commit()


def corrupt_balance(db, item, qty):
    """What a bug would do: change the recorded balance without a movement."""
    db.execute(
        update(StockBalance)
        .where(
            StockBalance.location_id == item.location_id,
            StockBalance.inventory_item_id == item.inventory_item_id,
        )
        .values(qty_base=D(qty))
    )
    db.commit()


def night(when=NIGHT):
    scheduler.queue_ledger_verification(when)
    return drain()


def drift_exceptions(db):
    db.expire_all()
    return list(db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "balance_drift")))


def ledger_state(db):
    db.expire_all()
    return (
        [
            (b.location_id, b.inventory_item_id, b.qty_base)
            for b in db.scalars(select(StockBalance))
        ],
        db.scalar(select(func.count(InventoryMovement.id))),
        [s.qty_base for s in db.scalars(select(StockItem).order_by(StockItem.id))],
    )


# --- the rule (pure) ---------------------------------------------------------------------------


def test_drift_is_any_pair_whose_balance_movements_or_screen_disagree():
    pair, other, third, fourth = (1, 10), (1, 11), (2, 10), (2, 11)
    found = find_drift(
        recorded={pair: D(5), other: D(3), third: D(7)},
        movements={pair: D(5), other: D(2), fourth: D(1)},
        shown={pair: D(5), third: D(6)},
    )
    assert found == [
        Drift(1, 11, D(3), D(2), None),  # balance is not the sum of its movements
        Drift(2, 10, D(7), D(0), D(6)),  # neither: no movements, and the screen differs
        Drift(2, 11, D(0), D(1), None),  # movements with no balance row at all
    ]
    assert find_drift({pair: D(5)}, {pair: D(5)}, {pair: D(5)}) == []


# --- AC-INV-11 ---------------------------------------------------------------------------------


def test_ac_inv_11_an_injected_mismatch_is_found_the_same_night_and_nothing_changes(
    db, tenant, location, caplog
):
    milk = stock(db, location)
    receive(db, milk, "6", "delivery")
    corrupt_balance(db, milk, "999")
    before = ledger_state(db)

    with caplog.at_level(logging.ERROR, logger="app.jobs"):
        assert night()["done"] == 1

    [drift] = drift_exceptions(db)
    assert (drift.severity, drift.status, drift.location_id) == ("critical", "open", location.id)
    assert drift.subject_id == f"{milk.inventory_item_id}:{location.id}"
    assert drift.title == (
        "Oat Milk at Main: the recorded balance is 999 ml but its movements add up to 18 ml,"
        " and the screens show 18 ml. Nothing was changed."
    )
    assert ledger_state(db) == before  # DR-03: reported, never overwritten
    alert = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(alert) == 1 and "ledger drift at 1 item location(s)" in alert[0].getMessage()
    assert "Oat" not in alert[0].getMessage()  # ids only in the log


def test_a_screen_out_of_step_with_its_balance_is_drift_too(db, tenant, location):
    milk = stock(db, location)
    db.get(StockItem, milk.id).qty_base = D("40")  # a writer that bypassed the posting service
    db.commit()
    night()
    [drift] = drift_exceptions(db)
    assert "the recorded balance is 12 ml but its movements add up to 12 ml" in drift.title
    assert "the screens show 40 ml" in drift.title


def test_a_healthy_ledger_raises_nothing_and_a_fixed_one_resolves(db, tenant, location):
    milk = stock(db, location)
    receive(db, milk, "6", "delivery")
    night()
    assert drift_exceptions(db) == []

    corrupt_balance(db, milk, "1")
    night(NIGHT.replace(day=14))
    [drift] = drift_exceptions(db)
    assert drift.status == "open"

    corrupt_balance(db, milk, "18")  # support rebuilt it from the movements
    night(NIGHT.replace(day=15))
    [drift] = drift_exceptions(db)
    assert (drift.status, drift.resolution) == ("resolved", "system")


def test_each_business_is_checked_once_a_night_and_only_its_own_ledger(db, tenant, location):
    other = Tenant(name="Harbor Cafe")
    db.add(other)
    db.flush()
    harbor = Location(tenant_id=other.id, name="Harbor", timezone="UTC")
    db.add(harbor)
    db.commit()
    stock(db, location)
    theirs = stock(db, harbor, "cold-brew", "3")
    corrupt_balance(db, theirs, "30")

    scheduler.queue_ledger_verification(NIGHT)
    scheduler.queue_ledger_verification(NIGHT)  # a retried scheduler run the same night
    db.expire_all()
    queued = db.scalars(select(Job).where(Job.kind == "verify_ledger")).all()
    assert sorted(job.tenant_id for job in queued) == sorted([tenant.id, other.id])
    drain()
    [drift] = drift_exceptions(db)
    assert drift.tenant_id == other.id


def test_the_scheduler_queues_the_verification_every_night():
    class Recorder:
        def __init__(self):
            self.jobs = {}

        def add_job(self, func, trigger, *args, id, **kwargs):
            self.jobs[id] = (trigger, kwargs)

    recorder = Recorder()
    scheduler._add_jobs(recorder)
    trigger, kwargs = recorder.jobs["queue_ledger_verification"]
    assert (trigger, kwargs["hour"], kwargs["minute"]) == ("cron", 2, 45)
