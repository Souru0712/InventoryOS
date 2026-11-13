"""T2.02-a: versioned recipes, model and versioning (RCP-FR-02, -04..09, -14, DR-11, DR-16,
DR-17, AC-RCP-01, -02, -05, -06)."""

import argparse
import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from app.access import OrgContext, UnscopedAccess, update_scoped
from app.access.permissions import role_permissions
from app.cli import cmd_demo
from app.db import Base, SessionLocal, engine
from app.items import NewItem, add_conversion, create_item
from app.models import (
    AuditEvent,
    ProductVariation,
    Recipe,
    RecipeComponent,
    RecipeLine,
    RecipeVersion,
    StockItem,
)
from app.products import NewVariation, create_product, save_modifier
from app.recipes import NewLine, Owner, effective_version, save_recipe, start_from, versions
from app.transform.recipes import (
    Line,
    RecipeRuleError,
    check_effective_from,
    find_cycle,
    scale_lines,
    size_ratio,
    version_at,
)

D = Decimal
NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


@pytest.fixture
def kitchen(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("manager"))

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
        "beans": item("Espresso beans", "weight", "g"),
        "cup": item("Cup 16 oz", "count", "each", "packaging"),
        "ginger": item("Ginger", "weight", "g"),
        "sugar": item("Sugar", "weight", "g"),
        "juice": item("Ginger juice", "volume", "ml", "prepared"),
        "syrup": item("Ginger syrup", "volume", "ml", "prepared"),
    }
    golden = create_product(
        db, org, name="Golden State", variations=[NewVariation("12 oz"), NewVariation("16 oz")]
    )
    db.commit()
    return org, made, golden


def line(item, quantity, unit=""):
    return NewLine(item.id, quantity, unit)


# --- the rules (pure) --------------------------------------------------------------------------


def test_the_version_in_effect_is_the_latest_started_by_then():
    history = [(1, NOW), (2, NOW + timedelta(hours=1)), (3, NOW + timedelta(hours=1))]
    assert version_at(history, NOW - timedelta(seconds=1)) is None
    assert version_at(history, NOW + timedelta(minutes=30)) == 1
    assert version_at(history, NOW + timedelta(hours=2)) == 3  # a tie goes to the later save
    with pytest.raises(RecipeRuleError, match="the past keeps its recipe"):
        check_effective_from(NOW - timedelta(hours=1), NOW)
    assert check_effective_from(None, NOW) == NOW


def test_sizes_scale_by_the_amounts_their_names_give():
    assert size_ratio("12 oz", "16 oz") == D(16) / D(12)
    assert size_ratio("500ml", "750 ml") == D("1.5")
    assert size_ratio("Small", "Large") == 1  # nothing comparable: start unscaled
    assert size_ratio("12 oz", "500 ml") == 1
    [scaled] = scale_lines([Line(1, D(12), "g", D(12))], D(16) / D(12))
    assert (scaled.quantity, scaled.qty_base) == (D("16.0000"), D("16.0000"))


def test_a_circle_through_prepared_items_is_found():
    uses = {10: [11], 11: [12]}  # A uses B, B uses C
    assert find_cycle(12, uses, [10]) == [12, 10, 11, 12]  # C using A closes the circle
    assert find_cycle(12, uses, [11, 99]) == [12, 11, 12]
    assert find_cycle(13, uses, [10]) == []


# --- AC-RCP-01: mixed units, saved only when every unit converts -------------------------------


def test_ac_rcp_01_a_recipe_in_mixed_units_saves_only_if_every_unit_converts(kitchen, db):
    org, item, golden = kitchen
    large = golden.variations[1]
    version = save_recipe(
        db,
        org,
        Owner("variation", large.id),
        [line(item["milk"], "0.25", "l"), line(item["beans"], "18", "g"), line(item["cup"], "1")],
    )
    db.commit()
    assert [
        (ln.item.name, ln.quantity, ln.unit, ln.qty_base, ln.is_packaging) for ln in version.lines
    ] == [
        ("Milk", D("0.25"), "l", D("250.0000"), False),
        ("Espresso beans", D("18"), "g", D("18.0000"), False),
        ("Cup 16 oz", D("1"), "each", D("1.0000"), True),  # packaging by its type
    ]
    with pytest.raises(RecipeRuleError, match="Ginger"):  # names the missing conversion
        save_recipe(
            db,
            org,
            Owner("variation", large.id),
            [line(item["milk"], "250", "ml"), line(item["ginger"], "120", "ml")],
        )
    db.rollback()
    assert [v.version_no for v in versions(db, org, version.recipe)] == [1]  # nothing written
    add_conversion(
        db,
        org,
        item["ginger"].id,
        from_quantity="1",
        from_unit="lb",
        to_quantity="340",
        to_unit="ml",
    )
    second = save_recipe(
        db,
        org,
        Owner("variation", large.id),
        [line(item["milk"], "250", "ml"), line(item["ginger"], "120", "ml")],
    )
    assert second.version_no == 2
    assert second.lines[1].qty_base.quantize(D("0.01")) == D("160.09")  # AC-INV-03's figure


def test_a_line_needs_an_amount_and_each_item_once(kitchen, db):
    org, item, golden = kitchen
    owner = Owner("variation", golden.variations[0].id)
    for lines, message in (
        ([], "at least one ingredient"),
        ([line(item["milk"], "0", "ml")], "above zero"),
        ([line(item["milk"], "lots", "ml")], "a number"),
        ([line(item["milk"], "1", "ml"), line(item["milk"], "2", "ml")], "listed twice"),
    ):
        with pytest.raises(RecipeRuleError, match=message):
            save_recipe(db, org, owner, lines)


# --- AC-RCP-02: a sale uses the version in effect at its own time ------------------------------


def test_ac_rcp_02_an_edit_is_version_2_and_earlier_sales_keep_version_1(kitchen, db):
    org, item, golden = kitchen
    owner = Owner("variation", golden.variations[0].id)
    first = save_recipe(db, org, owner, [line(item["milk"], "200", "ml")])
    later = datetime.now(UTC) + timedelta(hours=1)
    second = save_recipe(db, org, owner, [line(item["milk"], "240", "ml")], effective_from=later)
    db.commit()
    recipe = first.recipe
    assert effective_version(db, org, recipe, later - timedelta(minutes=30)).version_no == 1
    assert effective_version(db, org, recipe, later + timedelta(minutes=1)).version_no == 2
    # A sale that happened before the edit but arrives after it still reads version 1.
    db.expire_all()
    late_arrival = later - timedelta(minutes=5)
    assert effective_version(db, org, recipe, late_arrival).lines[0].qty_base == D("200.0000")
    assert [ln.qty_base for ln in second.lines] == [D("240.0000")]
    assert save_recipe(db, org, owner, [line(item["milk"], "240", "ml")]) is None  # no change
    with pytest.raises(RecipeRuleError, match="the past keeps its recipe"):
        save_recipe(
            db, org, owner, [line(item["milk"], "1", "ml")], effective_from=NOW - timedelta(days=9)
        )


# --- AC-RCP-05: prepared items, but no circles -------------------------------------------------


def test_ac_rcp_05_a_recipe_may_use_a_prepared_item_but_not_in_a_circle(kitchen, db):
    org, item, golden = kitchen
    save_recipe(db, org, Owner("item", item["syrup"].id), [line(item["sugar"], "500", "g")])
    save_recipe(
        db,
        org,
        Owner("item", item["juice"].id),
        [line(item["ginger"], "450", "g"), line(item["syrup"], "100", "ml")],
        yield_quantity="0.5",
        yield_unit="l",
    )
    drink = save_recipe(
        db, org, Owner("variation", golden.variations[0].id), [line(item["juice"], "60", "ml")]
    )
    assert drink.lines[0].item.name == "Ginger juice"  # a prepared item as an ingredient
    with pytest.raises(RecipeRuleError, match="Ginger syrup uses Ginger juice uses Ginger syrup"):
        save_recipe(db, org, Owner("item", item["syrup"].id), [line(item["juice"], "10", "ml")])
    with pytest.raises(RecipeRuleError, match="circle"):
        save_recipe(db, org, Owner("item", item["juice"].id), [line(item["juice"], "1", "ml")])
    made = db.scalars(select(Recipe).where(Recipe.inventory_item_id == item["juice"].id)).one()
    juice = effective_version(db, org, made)
    assert (juice.yield_quantity, juice.yield_unit, juice.yield_base) == (
        D("0.5000"),
        "l",
        D("500.0000"),
    )


def test_only_sizes_add_modifiers_and_made_items_have_recipes(kitchen, db):
    org, item, golden = kitchen
    ice = save_modifier(db, org, name="Light ice", group="", effect="scale", scale_factor="1.15")
    shot = save_modifier(db, org, name="Ginger shot", group="", effect="add", scale_factor="")
    with pytest.raises(RecipeRuleError, match="adds ingredients"):
        save_recipe(db, org, Owner("modifier", ice.id), [line(item["milk"], "1", "ml")])
    added = save_recipe(db, org, Owner("modifier", shot.id), [line(item["juice"], "30", "ml")])
    assert added.yield_unit == "serving"
    with pytest.raises(RecipeRuleError, match="prepared or finished"):
        save_recipe(db, org, Owner("item", item["milk"].id), [line(item["sugar"], "1", "g")])


# --- AC-RCP-06: start from another size --------------------------------------------------------


def test_ac_rcp_06_start_from_12_oz_fills_16_oz_at_16_twelfths(kitchen, db):
    org, item, golden = kitchen
    small, large = golden.variations
    save_recipe(
        db,
        org,
        Owner("variation", small.id),
        [line(item["juice"], "120", "ml"), line(item["beans"], "9", "g")],
    )
    filled = start_from(db, org, small.id, large.id)
    assert [(n.inventory_item_id, n.quantity, n.unit) for n in filled] == [
        (item["juice"].id, "160", "ml"),
        (item["beans"].id, "12", "g"),
    ]
    with pytest.raises(RecipeRuleError, match="no recipe"):
        start_from(db, org, large.id, small.id)


# --- immutable, one writer, recorded -----------------------------------------------------------


def test_versions_are_append_only_written_in_one_place_and_recorded(kitchen, db):
    org, item, golden = kitchen
    save_recipe(db, org, Owner("variation", golden.variations[0].id), [line(item["milk"], "1")])
    db.commit()
    recorded = db.scalars(select(AuditEvent.action).where(AuditEvent.action.like("recipe.%")))
    assert recorded.all() == ["recipe.saved"]
    for model in (RecipeVersion, RecipeLine):
        with pytest.raises(UnscopedAccess, match="append-only"):
            update_scoped(OrgContext.system(1), model)
    found = []
    for path in sorted(Path("app").rglob("*.py")):
        if path.as_posix() == "app/recipes.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = getattr(getattr(node, "func", None), "id", "")
            if isinstance(node, ast.Call) and name in {"Recipe", "RecipeVersion", "RecipeLine"}:
                found.append(f"{path.as_posix()}:{node.lineno}")
    assert found == []


# --- DM-9: the demo's recipe components are each version 1 -------------------------------------


def test_the_demo_cafe_recipes_are_version_1_of_its_components():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    args = argparse.Namespace(
        email="demo@stockpulse.local", password="demopassword", name="Rosewood Coffee", days=1
    )
    assert cmd_demo(args) == 0
    with SessionLocal() as db:
        variation_of = {v.source_sold_item_id: v.id for v in db.scalars(select(ProductVariation))}
        stock = {s.id: s.inventory_item_id for s in db.scalars(select(StockItem))}
        expected = {
            (variation_of[c.sold_item_id], stock[c.stock_item_id], D(c.qty_base_per_sale))
            for c in db.scalars(select(RecipeComponent))
        }
        found = {
            (v.recipe.product_variation_id, ln.inventory_item_id, D(ln.qty_base))
            for v in db.scalars(select(RecipeVersion))
            for ln in v.lines
        }
        assert expected and found == expected
        assert {v.version_no for v in db.scalars(select(RecipeVersion))} == {1}
