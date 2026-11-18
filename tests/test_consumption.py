"""T2.05: the consumption engine (WF-12, POS-FR-07, PRD-FR-11, DR-18, AC-RCP-04, AC-PRD-04):
the resolver (T2.05-a), and posting what each sale used to the ledger (T2.05-b)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.consumption import SOURCE, consume_sale, reprocess_sale, resolve_sale, sale_moment
from app.exceptions import listing
from app.items import NewItem, create_item, stock_at
from app.jobs import _handlers, drain
from app.ledger.posting import balance, post_count, standing
from app.models import AuditEvent, ExceptionRecord, Job, Location
from app.products import NewVariation, create_product, save_modifier, update_product
from app.recipes import NewLine, Owner, save_recipe
from app.sales import add_source, record_sale
from app.transform.consumption import (
    ADD_WITHOUT_RECIPE,
    NO_RECIPE,
    NOT_MADE,
    UNMAPPED,
    LineIn,
    ModifierIn,
    RecipeIn,
    resolve,
)
from app.transform.products import ProductRuleError
from app.transform.sales import SaleLine

D = Decimal
MILK, CUP, SYRUP, JUICE, GINGER, SUGAR = 1, 2, 3, 4, 5, 6
DRINK = RecipeIn(10, ((MILK, D(240), False), (CUP, D(1), True)))
VANILLA = RecipeIn(20, ((SYRUP, D(10), False),))  # one pump
GOLDEN = RecipeIn(30, ((JUICE, D(480), False), (CUP, D(1), True)))
JUICE_RECIPE = RecipeIn(40, ((GINGER, D(450), False),), D(500))  # 450 g makes 500 ml


def ice(times=1):
    return ModifierIn(7, "scale", D("1.15"), times=times)


def pumps(times):
    return ModifierIn(8, "add", recipe=VANILLA, times=times)


# --- the rules, table by table (pure) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "uses"),
    [
        # two plain drinks: twice the recipe, cups included
        (LineIn(D(2), DRINK), {MILK: D(480), CUP: D(2)}),
        # a recipe that makes two servings: each sale is half of it
        (LineIn(D(1), RecipeIn(11, ((MILK, D(500), False),), D(2))), {MILK: D(250)}),
        # AC-RCP-04: light ice makes the drink's lines 115 %, the cup stays one cup
        (LineIn(D(1), DRINK, modifiers=(ice(),)), {MILK: D(276), CUP: D(1)}),
        # AC-RCP-04: an add-in adds its own lines, unscaled; two pumps on three drinks
        (
            LineIn(D(3), DRINK, modifiers=(ice(), pumps(2))),
            {MILK: D(828), CUP: D(3), SYRUP: D(60)},
        ),
        # a modifier that changes nothing
        (LineIn(D(1), DRINK, modifiers=(ModifierIn(9, "none"),)), {MILK: D(240), CUP: D(1)}),
    ],
)
def test_a_sold_line_uses_its_recipe_scaled_and_added_to(line, uses):
    result = resolve(line)
    assert result.uses == uses and result.complete


def test_ac_prd_04_made_ahead_uses_the_prepared_item_made_to_order_its_ingredients():
    ahead = resolve(LineIn(D(1), GOLDEN, made_to_order=False), {JUICE: JUICE_RECIPE}, {JUICE})
    assert ahead.uses == {JUICE: D(480), CUP: D(1)}  # the juice itself, nothing it is made of
    to_order = resolve(LineIn(D(1), GOLDEN), {JUICE: JUICE_RECIPE}, {JUICE})
    assert to_order.uses == {GINGER: D(432), CUP: D(1)}  # 480/500 of the juice's recipe
    assert JUICE not in to_order.uses  # never both (DR-18)
    assert to_order.versions == (30, 40)


def test_a_prepared_item_inside_a_prepared_item_is_made_from_what_it_is_made_of():
    syrup = RecipeIn(50, ((SUGAR, D(500), False),), D(1000))  # 500 g of sugar makes 1 l
    juice = RecipeIn(41, ((GINGER, D(450), False), (SYRUP, D(100), False)), D(500))
    result = resolve(LineIn(D(1), GOLDEN), {JUICE: juice, SYRUP: syrup}, {JUICE, SYRUP})
    assert result.uses == {GINGER: D(432), SUGAR: D(48), CUP: D(1)}


def test_what_cannot_be_resolved_is_a_gap_and_the_rest_is_still_used():
    assert resolve(LineIn(D(1), None, mapped=False)).gaps == (UNMAPPED,)
    assert resolve(LineIn(D(1), None)).gaps == (NO_RECIPE,)
    no_add = resolve(LineIn(D(1), DRINK, modifiers=(ModifierIn(8, "add", times=2),)))
    assert no_add.gaps == (ADD_WITHOUT_RECIPE,) and no_add.uses == {MILK: D(240), CUP: D(1)}
    unmade = resolve(LineIn(D(1), GOLDEN), {}, {JUICE})  # made to order, no juice recipe
    assert unmade.gaps == (NOT_MADE,) and unmade.uses == {JUICE: D(480), CUP: D(1)}


# --- from a recorded sale, with the versions in effect then ------------------------------------


@pytest.fixture
def bar(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))

    def item(name, dimension, unit, item_type="raw", cost="0.01"):
        return create_item(
            db,
            org,
            NewItem(
                name=name,
                location_ids=[location.id],
                dimension=dimension,
                display_unit=unit,
                item_type=item_type,
                cost_per_unit=cost,
            ),
        )

    made = {
        "milk": item("Milk", "volume", "ml"),
        "cup": item("Cup", "count", "each", "packaging"),
        "syrup": item("Vanilla syrup", "volume", "ml", cost="0"),  # no cost yet
        "ginger": item("Ginger", "weight", "g"),
        "juice": item("Golden juice", "volume", "ml", "prepared"),
    }
    golden = create_product(db, org, name="Golden State", variations=[NewVariation("16 oz")])
    latte = create_product(db, org, name="Latte", variations=[NewVariation("12 oz")])
    ice = save_modifier(db, org, name="Light ice", group="", effect="scale", scale_factor="1.15")
    vanilla = save_modifier(db, org, name="Vanilla pump", group="", effect="add", scale_factor="")

    def recipe(owner, *lines, **extra):
        return save_recipe(db, org, owner, [NewLine(i.id, q, u) for i, q, u in lines], **extra)

    recipe(
        Owner("variation", latte.variations[0].id),
        (made["milk"], "240", "ml"),
        (made["cup"], "1", ""),
    )
    recipe(Owner("modifier", vanilla.id), (made["syrup"], "10", "ml"))
    recipe(
        Owner("variation", golden.variations[0].id),
        (made["juice"], "480", "ml"),
        (made["cup"], "1", ""),
    )
    recipe(
        Owner("item", made["juice"].id),
        (made["ginger"], "450", "g"),
        yield_quantity="500",
        yield_unit="ml",
    )
    source = add_source(
        db, org, location_id=location.id, kind="manual", active_from=date(2026, 1, 1)
    )
    db.commit()
    return org, made, golden, latte, ice, vanilla, source


def test_ac_rcp_04_a_recorded_latte_with_light_ice_and_two_pumps(db, bar):
    org, item, _golden, latte, ice, vanilla, source = bar
    sale = record_sale(
        db,
        org,
        source,
        source_reference="o-1",
        occurred_at=datetime.now(UTC),
        lines=[
            SaleLine("1", D(2), latte.variations[0].id, modifiers=((ice.id, 1), (vanilla.id, 2)))
        ],
    )
    [used] = resolve_sale(db, org, sale).values()
    assert used.uses == {item["milk"].id: D(552), item["cup"].id: D(2), item["syrup"].id: D(40)}


def test_ac_prd_04_the_products_mode_decides_and_never_both(db, bar):
    org, item, golden, _latte, _ice, _vanilla, source = bar
    sale = record_sale(
        db,
        org,
        source,
        source_reference="o-2",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(1), golden.variations[0].id)],
    )
    [to_order] = resolve_sale(db, org, sale).values()
    assert to_order.uses == {item["ginger"].id: D(432), item["cup"].id: D(1)}
    update_product(
        db, org, golden.id, name="Golden State", category="", production_mode="made_ahead"
    )
    db.commit()
    [ahead] = resolve_sale(db, org, sale).values()
    assert ahead.uses == {item["juice"].id: D(480), item["cup"].id: D(1)}
    changed = db.scalars(select(AuditEvent).where(AuditEvent.action == "product.updated")).one()
    assert changed.after == {"mode": "made_ahead"}
    with pytest.raises(ProductRuleError, match="made to order or made ahead"):
        update_product(db, org, golden.id, name="Golden State", category="", production_mode="x")


def test_a_sale_reads_the_recipe_in_effect_when_it_happened(db, bar, location):
    org, item, _golden, latte, _ice, _vanilla, source = bar
    size = latte.variations[0].id
    later = datetime.now(UTC) + timedelta(hours=2)
    save_recipe(
        db,
        org,
        Owner("variation", size),
        [NewLine(item["milk"].id, "300", "ml")],
        effective_from=later,
    )
    before = record_sale(
        db,
        org,
        source,
        source_reference="early",
        occurred_at=later - timedelta(hours=1),
        lines=[SaleLine("1", D(1), size)],
    )
    after = record_sale(
        db,
        org,
        source,
        source_reference="late",
        occurred_at=later + timedelta(minutes=1),
        lines=[SaleLine("1", D(1), size)],
    )
    assert list(resolve_sale(db, org, before).values())[0].uses[item["milk"].id] == D(240)
    assert list(resolve_sale(db, org, after).values())[0].uses[item["milk"].id] == D(300)
    # A day's sales entered as a whole read the version in effect at the end of that day.
    day = record_sale(
        db,
        org,
        source,
        source_reference="day",
        day=date(2026, 3, 2),
        lines=[SaleLine("1", D(1), size)],
    )
    assert sale_moment(db, org, day) == datetime(2026, 3, 2, 23, 59, 59, 999999, tzinfo=UTC)


def test_an_unmapped_pos_line_is_a_gap_not_a_lost_sale(db, bar):
    org, *_rest, source = bar
    sale = record_sale(
        db,
        org,
        source,
        source_reference="o-3",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(4), external_ref="SQ-VAR-9", description="Matcha")],
    )
    [used] = resolve_sale(db, org, sale).values()
    assert (used.uses, used.gaps) == ({}, (UNMAPPED,))


# --- posting what a sale used (T2.05-b) --------------------------------------------------------


def _run_jobs(**kwargs):
    return drain(handlers={**_handlers(), "send_alert_digest": lambda *_: None}, **kwargs)


def _held(db, org, place, item):
    return balance(db, org, place.id, item.id)


def _open(db, type_, subject_type, subject_id):
    return db.scalars(
        select(ExceptionRecord).where(
            ExceptionRecord.type == type_,
            ExceptionRecord.subject_type == subject_type,
            ExceptionRecord.subject_id == subject_id,
        )
    ).one_or_none()


def _annex(db, tenant):
    annex = Location(tenant_id=tenant.id, name="Annex", timezone="UTC")
    db.add(annex)
    db.flush()
    return annex


def _oat_latte(db, org, item, annex):
    """A drink made with an item that only the other location stocks."""
    oat = create_item(
        db,
        org,
        NewItem(
            name="Oat milk",
            location_ids=[annex.id],
            dimension="volume",
            display_unit="ml",
            cost_per_unit="0.01",
        ),
    )
    drink = create_product(db, org, name="Oat latte", variations=[NewVariation("12 oz")])
    save_recipe(
        db,
        org,
        Owner("variation", drink.variations[0].id),
        [NewLine(oat.id, "200", "ml"), NewLine(item["cup"].id, "1", "")],
    )
    return oat, drink


def test_a_sale_leaves_stock_once_at_its_own_moment(db, bar, location):
    org, item, _golden, latte, ice, vanilla, source = bar
    at = datetime.now(UTC)  # the recipes took effect when the fixture saved them
    sale = record_sale(
        db,
        org,
        source,
        source_reference="o-1",
        occurred_at=at,
        lines=[
            SaleLine("1", D(2), latte.variations[0].id, modifiers=((ice.id, 1), (vanilla.id, 2)))
        ],
    )
    db.commit()
    assert _run_jobs()["done"] >= 1
    db.expire_all()
    assert _held(db, org, location, item["milk"]) == D(-552)
    assert _held(db, org, location, item["cup"]) == D(-2)
    assert _held(db, org, location, item["syrup"]) == D(-40)
    [posting] = standing(db, org, SOURCE, str(sale.id))
    assert posting.idempotency_key == f"sale:{sale.id}:r1"
    assert posting.kind == "sale" and posting.occurred_at == at
    assert {(m.location_id, m.movement_type) for m in posting.movements} == {
        (location.id, "sale_consumption")
    }
    [line] = sale.lines
    assert line.consumption_status == "posted" and line.recipe_version_id is not None
    # Run again, by hand or by the queue: nothing more leaves stock (WF-12: idempotent).
    assert consume_sale(db, org, sale.id) is False
    db.commit()
    _run_jobs()
    db.expire_all()
    assert _held(db, org, location, item["milk"]) == D(-552)
    assert len(standing(db, org, SOURCE, str(sale.id))) == 1
    # The syrup has no cost: the sale is posted all the same, and someone is asked for one.
    uncosted = _open(db, "missing_cost", "item_location", f"{item['syrup'].id}:{location.id}")
    assert uncosted is not None and uncosted.status == "open"


def test_a_sale_at_one_location_never_touches_anothers_stock(db, bar, tenant, location):
    org, item, _golden, latte, _ice, _vanilla, _source = bar
    annex = _annex(db, tenant)
    for name in ("milk", "cup"):
        stock_at(db, org, item[name].id, annex.id)
    there = add_source(db, org, location_id=annex.id, kind="manual", active_from=date(2026, 1, 1))
    record_sale(
        db,
        org,
        there,
        source_reference="a-1",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(1), latte.variations[0].id)],
    )
    db.commit()
    _run_jobs()
    db.expire_all()
    assert _held(db, org, annex, item["milk"]) == D(-240)
    assert _held(db, org, location, item["milk"]) == D(0)


def test_a_sale_after_a_same_day_count_is_consumed(db, bar, location):
    org, item, _golden, latte, _ice, _vanilla, source = bar
    counted_at = datetime.now(UTC)
    post_count(
        db,
        org,
        location_id=location.id,
        inventory_item_id=item["milk"].id,
        counted=D(5000),
        source_type="test",
        source_id="morning",
        idempotency_key="count:morning",
        occurred_at=counted_at,
    )
    record_sale(
        db,
        org,
        source,
        source_reference="o-after",
        occurred_at=counted_at + timedelta(minutes=1),
        lines=[SaleLine("1", D(1), latte.variations[0].id)],
    )
    db.commit()
    _run_jobs()
    db.expire_all()
    assert _held(db, org, location, item["milk"]) == D(4760)


def test_a_corrected_sale_gives_back_what_the_first_revision_took(db, bar, location):
    org, item, _golden, latte, _ice, _vanilla, source = bar
    size = latte.variations[0].id
    at = datetime.now(UTC)
    first = record_sale(
        db, org, source, source_reference="o-9", occurred_at=at, lines=[SaleLine("1", D(1), size)]
    )
    db.commit()
    _run_jobs()
    second = record_sale(
        db, org, source, source_reference="o-9", occurred_at=at, lines=[SaleLine("1", D(3), size)]
    )
    db.commit()
    _run_jobs()
    db.expire_all()
    assert second.revision == 2 and first.status == "corrected"
    assert _held(db, org, location, item["milk"]) == D(-720)  # three lattes, not four
    assert standing(db, org, SOURCE, str(first.id)) == []
    [posting] = standing(db, org, SOURCE, str(second.id))
    assert posting.idempotency_key == f"sale:{second.id}:r2"


def test_what_a_sale_cannot_use_is_a_gap_and_an_exception_never_a_lost_sale(
    db, bar, tenant, location
):
    org, item, _golden, latte, _ice, _vanilla, source = bar
    oat, oat_latte = _oat_latte(db, org, item, _annex(db, tenant))
    muffin = create_product(db, org, name="Muffin", variations=[NewVariation("Regular")])
    lines = [
        SaleLine("1", D(2), external_ref="SQ-VAR-9", description="Matcha"),
        SaleLine("2", D(1), muffin.variations[0].id),
        SaleLine("3", D(1), oat_latte.variations[0].id),
        SaleLine("4", D(1), latte.variations[0].id),
    ]
    sale = record_sale(
        db, org, source, source_reference="o-gaps", occurred_at=datetime.now(UTC), lines=lines
    )
    db.commit()
    _run_jobs()
    db.expire_all()
    assert [ln.consumption_status for ln in sale.lines] == ["gap", "gap", "gap", "posted"]
    assert _held(db, org, location, item["cup"]) == D(-2)  # the oat latte's cup and the latte's
    assert _held(db, org, location, item["milk"]) == D(-240)
    matcha = _open(db, "unmapped_product", "pos_item", f"{location.id}:SQ-VAR-9")
    assert matcha.occurrences == 1 and "Matcha" in matcha.title
    no_recipe = _open(db, "unmapped_product", "product_variation", str(muffin.variations[0].id))
    assert "Muffin Regular" in no_recipe.title
    unstocked = _open(db, "unmapped_product", "item_location", f"{oat.id}:{location.id}")
    assert "not stocked at Main" in unstocked.title
    # Each exception opens the record to fix.
    links = {view.subject_type: view.link for view in listing(db, org)}
    assert links["product_variation"] == f"/app/products/{muffin.id}"
    assert links["item_location"] == f"/app/catalog/{oat.id}"
    # The next unmapped sale adds to the same exception: it counts the sales (WF-28).
    record_sale(
        db,
        org,
        source,
        source_reference="o-gaps-2",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(1), external_ref="SQ-VAR-9", description="Matcha")],
    )
    db.commit()
    _run_jobs()
    db.expire_all()
    assert _open(db, "unmapped_product", "pos_item", f"{location.id}:SQ-VAR-9").occurrences == 2


def test_reprocessing_posts_again_once_the_gap_is_fixed(db, bar, tenant, location):
    org, item, _golden, _latte, _ice, _vanilla, source = bar
    oat, drink = _oat_latte(db, org, item, _annex(db, tenant))
    sale = record_sale(
        db,
        org,
        source,
        source_reference="o-oat",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(1), drink.variations[0].id)],
    )
    db.commit()
    _run_jobs()
    db.expire_all()
    assert sale.lines[0].consumption_status == "gap"
    stock_at(db, org, oat.id, location.id)
    assert reprocess_sale(db, org, sale.id, reason="Oat milk stocked at Main") is True
    db.commit()
    db.expire_all()
    assert sale.lines[0].consumption_status == "posted"
    assert _held(db, org, location, item["cup"]) == D(-1)  # the first posting was given back
    assert _held(db, org, location, oat) == D(-200)
    [posting] = standing(db, org, SOURCE, str(sale.id))
    assert posting.idempotency_key == f"sale:{sale.id}:r1:p2"


def test_sales_not_counted_or_kept_as_history_are_never_consumed(db, bar, location):
    org, item, _golden, latte, _ice, _vanilla, source = bar
    size = latte.variations[0].id
    other = add_source(
        db,
        org,
        location_id=location.id,
        kind="file_import",
        active_from=date(2026, 1, 1),
        of_record=False,
    )
    flagged = record_sale(
        db,
        org,
        other,
        source_reference="f-1",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(1), size)],
    )
    history = record_sale(
        db,
        org,
        source,
        source_reference="old",
        day=date(2026, 3, 2),
        lines=[SaleLine("1", D(5), size)],
        consume=False,
    )
    db.commit()
    queued = db.scalars(select(Job.payload).where(Job.kind == "consume_sale")).all()
    assert {"sales_transaction_id": history.id} not in queued
    _run_jobs()
    db.expire_all()
    assert [ln.consumption_status for ln in flagged.lines + history.lines] == ["skipped"] * 2
    assert _held(db, org, location, item["milk"]) == D(0)


def test_a_failure_leaves_the_lines_pending_and_the_queue_tries_again(
    db, bar, location, monkeypatch
):
    org, item, _golden, latte, _ice, _vanilla, source = bar
    sale = record_sale(
        db,
        org,
        source,
        source_reference="o-retry",
        occurred_at=datetime.now(UTC),
        lines=[SaleLine("1", D(1), latte.variations[0].id)],
    )
    db.commit()

    def broken(*_args, **_kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr("app.consumption.post_transaction", broken)
    assert _run_jobs()["retried"] == 1
    db.expire_all()
    assert sale.lines[0].consumption_status == "pending"  # recorded, not lost, not consumed
    monkeypatch.undo()
    later = datetime.now(UTC) + timedelta(minutes=5)
    assert _run_jobs(clock=lambda: later)["done"] >= 1
    db.expire_all()
    assert sale.lines[0].consumption_status == "posted"
    assert _held(db, org, location, item["milk"]) == D(-240)
