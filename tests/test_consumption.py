"""T2.05-a: the consumption resolver (WF-12, POS-FR-07, PRD-FR-11, DR-18, AC-RCP-04, AC-PRD-04)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.consumption import resolve_sale, sale_moment
from app.items import NewItem, create_item
from app.models import AuditEvent
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

    def item(name, dimension, unit, item_type="raw"):
        return create_item(
            db,
            org,
            NewItem(
                name=name,
                location_ids=[location.id],
                dimension=dimension,
                display_unit=unit,
                item_type=item_type,
            ),
        )

    made = {
        "milk": item("Milk", "volume", "ml"),
        "cup": item("Cup", "count", "each", "packaging"),
        "syrup": item("Vanilla syrup", "volume", "ml"),
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
