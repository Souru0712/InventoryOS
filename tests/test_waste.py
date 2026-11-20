"""T2.07: waste (WST-FR-01..12, DR-33, AC-WST-01..08)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.batches import date_stock
from app.items import NewItem, create_item
from app.ledger.history import movement_totals
from app.ledger.posting import balance, batch_remaining, post_count
from app.models import (
    AuditEvent,
    ExceptionRecord,
    InventoryMovement,
    Membership,
    StockItem,
    User,
    WasteEvent,
)
from app.products import NewVariation, create_product
from app.recipes import NewLine, Owner, save_recipe
from app.security import hash_password
from app.transform.ledger import BelowZero
from app.transform.waste import WasteRuleError, report
from app.units import to_base
from app.waste import (
    add_reason,
    change_reason,
    ensure_reasons,
    found_short,
    logged,
    reasons,
    record_waste,
    void_waste,
)

D = Decimal
T0 = datetime.now(UTC).replace(microsecond=0)
TODAY = T0.date()


def _person(db, tenant, role):
    user = User(
        tenant_id=tenant.id, email=f"{role}@rosewood.test", password_hash=hash_password("x" * 12)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.flush()
    return OrgContext(tenant_id=tenant.id, user_id=user.id, permissions=role_permissions(role))


@pytest.fixture
def kitchen(db, tenant, location):
    owner = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    carrots = create_item(
        db,
        owner,
        NewItem(
            name="Carrots",
            location_ids=[location.id],
            dimension="weight",
            display_unit="lb",
            quantity="10",
            cost_per_unit="1.50",
        ),
    )
    staff = _person(db, tenant, "employee")
    manager = _person(db, tenant, "manager")
    spoilage = {r.code: r for r in ensure_reasons(db, owner)}
    db.commit()
    return owner, staff, manager, carrots, spoilage


def _waste(db, org, location, reasons_by_code, item, qty, unit="lb", code="spoilage", **kw):
    return record_waste(
        db,
        org,
        location_id=location.id,
        reason_id=reasons_by_code[code].id,
        inventory_item_id=item.id,
        quantity=qty,
        unit=unit,
        **kw,
    )


def test_ac_wst_02_waste_without_a_reason_is_refused(db, kitchen, location):
    _owner, staff, _manager, carrots, _ = kitchen
    with pytest.raises(WasteRuleError, match="Choose a reason"):
        record_waste(
            db,
            staff,
            location_id=location.id,
            reason_id=None,
            inventory_item_id=carrots.id,
            quantity="1",
            unit="lb",
        )
    with pytest.raises(WasteRuleError, match="above zero"):
        record_waste(
            db,
            staff,
            location_id=location.id,
            reason_id=kitchen[4]["spoilage"].id,
            inventory_item_id=carrots.id,
            quantity="0",
            unit="lb",
        )


def test_ac_wst_03_and_08_two_pounds_of_carrots_count_once_everywhere(db, kitchen, location):
    _owner, staff, _manager, carrots, why = kitchen
    two = to_base("2", "lb")
    event = _waste(db, staff, location, why, carrots, "2", now=T0)
    db.commit()
    assert (event.actor_user_id, event.reason_label, event.occurred_at) == (
        staff.user_id,
        "Spoilage",
        T0,
    )
    [movement] = db.scalars(
        select(InventoryMovement).where(InventoryMovement.transaction_id == event.transaction_id)
    )
    assert (movement.movement_type, movement.qty_base) == ("waste", -two)
    assert movement.unit_cost_snapshot > 0  # the cost at that moment travels with it
    assert balance(db, staff, location.id, carrots.id) == to_base("10", "lb") - two

    [entry] = logged(db, staff, since=TODAY, until=TODAY)
    [loss] = entry.losses
    assert loss.qty_base == two and loss.unit == "lb"
    summary = report(entry.losses)
    assert summary.by_item["Carrots"].qty_base == two  # exactly 2 lb, not 4
    assert summary.total_cost_cents == entry.cost_cents == loss.cost_cents == 300
    # Waste is never sales or production consumption.
    window = {location.id: (T0 - timedelta(days=1), T0 + timedelta(days=1))}
    taken = movement_totals(db, staff, window, ["sale_consumption", "production_input", "waste"])
    assert (taken["sale_consumption"].movements, taken["production_input"].movements) == (0, 0)
    assert taken["waste"].movements == 1


def test_ac_wst_04_a_wasted_product_takes_its_recipes_ingredients(db, kitchen, tenant, location):
    owner, staff, _manager, carrots, why = kitchen
    cup = create_item(
        db,
        owner,
        NewItem(
            name="Cup",
            location_ids=[location.id],
            dimension="count",
            display_unit="each",
            item_type="packaging",
            quantity="50",
        ),
    )
    golden = create_product(db, owner, name="Golden State", variations=[NewVariation("16 oz")])
    size = golden.variations[0]
    with pytest.raises(WasteRuleError, match="no recipe"):
        record_waste(
            db,
            staff,
            location_id=location.id,
            reason_id=why["remake"].id,
            product_variation_id=size.id,
            quantity="1",
        )
    save_recipe(
        db,
        owner,
        Owner("variation", size.id),
        [NewLine(carrots.id, "0.5", "lb"), NewLine(cup.id, "1", "")],
    )
    event = record_waste(
        db,
        staff,
        location_id=location.id,
        reason_id=why["remake"].id,
        product_variation_id=size.id,
        quantity="1",
    )
    db.commit()
    moved = {
        (m.inventory_item_id, m.movement_type): m.qty_base
        for m in db.scalars(
            select(InventoryMovement).where(
                InventoryMovement.transaction_id == event.transaction_id
            )
        )
    }
    assert moved == {(carrots.id, "waste"): -to_base("0.5", "lb"), (cup.id, "waste"): D(-1)}
    assert event.recipe_version_id is not None and len(event.lines) == 2
    [entry] = logged(db, staff, since=event.occurred_at.date(), until=event.occurred_at.date())
    assert {loss.product for loss in entry.losses} == {"Golden State 16 oz"}


def test_ac_wst_05_the_logger_voids_for_30_minutes_then_a_manager_with_a_reason(
    db, kitchen, location
):
    _owner, staff, manager, carrots, why = kitchen
    first = _waste(db, staff, location, why, carrots, "1", now=T0)
    # The window runs from when the entry was recorded, which is the clock's now, not T0.
    void_waste(db, staff, first.id, now=first.recorded_at + timedelta(minutes=10))
    assert first.status == "voided"
    assert balance(db, staff, location.id, carrots.id) == to_base("10", "lb")
    assert logged(db, staff, since=TODAY, until=TODAY) == []  # a voided entry nets to nothing

    second = _waste(db, staff, location, why, carrots, "1", now=T0)
    later = second.recorded_at + timedelta(minutes=31)
    with pytest.raises(WasteRuleError, match="a Manager voids it"):
        void_waste(db, staff, second.id, now=later)
    with pytest.raises(WasteRuleError, match="Say why"):
        void_waste(db, manager, second.id, now=later)
    void_waste(db, manager, second.id, reason="Logged on the wrong item", now=later)
    assert (second.status, second.void_reason) == ("voided", "Logged on the wrong item")
    with pytest.raises(WasteRuleError, match="already voided"):
        void_waste(db, manager, second.id, reason="again", now=later)


def test_waste_takes_the_chosen_batch_or_the_soonest_to_expire(db, kitchen, location):
    _owner, staff, _manager, carrots, why = kitchen
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == carrots.id)).one()
    later = date_stock(db, staff, stock, to_base("4", "lb"), TODAY + timedelta(days=9))
    sooner = date_stock(db, staff, stock, to_base("4", "lb"), TODAY + timedelta(days=2))
    chosen = _waste(db, staff, location, why, carrots, "1", batch_id=later.id)
    inferred = _waste(db, staff, location, why, carrots, "1")
    modes = {
        m.transaction_id: (m.batch_id, m.allocation_mode)
        for m in db.scalars(
            select(InventoryMovement).where(
                InventoryMovement.transaction_id.in_(
                    [chosen.transaction_id, inferred.transaction_id]
                )
            )
        )
    }
    assert modes[chosen.transaction_id] == (later.id, "explicit")
    assert modes[inferred.transaction_id] == (sooner.id, "inferred")
    assert batch_remaining(db, staff, [later.id, sooner.id]) == {
        later.id: to_base("3", "lb"),
        sooner.id: to_base("3", "lb"),
    }


def test_dr_08_an_employee_cannot_waste_below_zero_a_manager_can(db, kitchen, location):
    _owner, staff, manager, carrots, why = kitchen
    with pytest.raises(BelowZero):
        _waste(db, staff, location, why, carrots, "12")
    db.rollback()
    _waste(db, manager, location, why, carrots, "12")
    db.commit()
    below = db.scalars(
        select(ExceptionRecord).where(ExceptionRecord.type == "negative_balance")
    ).one()
    assert below.status == "open"


def test_ac_wst_06_and_07_the_report_and_what_counts_found_short(db, kitchen, location):
    owner, staff, manager, carrots, why = kitchen
    onions = create_item(
        db,
        owner,
        NewItem(
            name="Onions",
            location_ids=[location.id],
            dimension="weight",
            display_unit="lb",
            quantity="10",
            cost_per_unit="3",
        ),
    )
    _waste(db, staff, location, why, carrots, "2", now=T0)
    _waste(db, manager, location, why, onions, "1", code="damaged", now=T0)
    post_count(
        db,
        owner,
        location_id=location.id,
        inventory_item_id=carrots.id,
        counted=to_base("7", "lb"),
        source_type="test",
        source_id="shelf",
        idempotency_key="count:shelf",
        occurred_at=T0 + timedelta(minutes=5),
    )
    db.commit()
    entries = logged(db, owner, since=TODAY, until=TODAY)
    summary = report(loss for entry in entries for loss in entry.losses)
    assert {k: v.cost_cents for k, v in summary.by_reason.items()} == {
        "Spoilage": 300,
        "Damaged goods": 300,
    }
    assert set(summary.by_person) == {"employee@rosewood.test", "manager@rosewood.test"}
    assert [name for name, _ in summary.top_items()] == ["Carrots", "Onions"]  # tie, by name
    # Counts found 1 lb of carrots missing beyond the 2 lb logged: shown apart, never summed in.
    [short] = found_short(db, owner, since=TODAY, until=TODAY)
    assert (short.item, short.qty_base, short.cost_cents) == ("Carrots", to_base("1", "lb"), 150)
    assert summary.by_item["Carrots"].qty_base == to_base("2", "lb")
    only_onions = logged(db, owner, since=TODAY, until=TODAY, item_id=onions.id)
    assert [e.losses[0].item for e in only_onions] == ["Onions"]


def test_reasons_are_renamed_and_archived_and_past_waste_keeps_its_name(db, kitchen, location):
    owner, staff, _manager, carrots, why = kitchen
    assert [r.label for r in reasons(db, owner)][:2] == ["Spoilage", "Past its date"]
    event = _waste(db, staff, location, why, carrots, "1")
    change_reason(db, owner, why["spoilage"].id, label="Gone off")
    assert event.reason_label == "Spoilage"
    add_reason(db, owner, "Dropped on the floor")
    with pytest.raises(WasteRuleError, match="already a reason"):
        add_reason(db, owner, "gone off")
    change_reason(db, owner, why["other"].id, active=False)
    assert "Other" not in [r.label for r in reasons(db, owner)]
    actions = sorted(
        db.scalars(select(AuditEvent.action).where(AuditEvent.action.like("settings.waste_%")))
    )
    assert actions == [
        "settings.waste_reason_added",
        "settings.waste_reason_changed",
        "settings.waste_reason_changed",
    ]


def test_waste_of_an_item_with_no_cost_is_kept_and_its_cost_is_unknown(db, kitchen, location):
    owner, staff, _manager, _carrots, why = kitchen
    herbs = create_item(
        db,
        owner,
        NewItem(
            name="Herbs",
            location_ids=[location.id],
            dimension="count",
            display_unit="each",
            quantity="5",
        ),
    )
    event = _waste(db, staff, location, why, herbs, "1", unit="")
    db.commit()
    # The day it was recorded, not the day the module loaded: a run can cross midnight.
    day = event.occurred_at.date()
    [entry] = logged(db, staff, since=day, until=day)
    assert entry.cost_cents is None
    assert (
        db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "missing_cost"))
        .one()
        .status
        == "open"
    )


def test_logging_through_the_pages(db, kitchen, location):
    _owner, _staff, _manager, carrots, why = kitchen
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        picker = client.get("/app/waste")
        assert picker.status_code == 200 and "Carrots" in picker.text
        form = client.get(f"/app/waste?item={carrots.id}")
        assert 'name="reason_id"' in form.text and 'name="quantity"' in form.text
        done = client.post(
            "/app/waste",
            data={
                "location_id": location.id,
                "inventory_item_id": carrots.id,
                "quantity": "1.5",
                "unit": "",
                "reason_id": why["spilled"].id,
            },
        )
        assert "Waste logged" in done.text
        log = client.get("/app/waste/log")
        assert (
            log.status_code == 200 and "Carrots" in log.text and "Found short at counts" in log.text
        )
    db.expire_all()
    assert db.scalars(select(WasteEvent)).one().qty_base == to_base("1.5", "lb")
