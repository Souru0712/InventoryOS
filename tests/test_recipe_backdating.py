"""C-51: a product size's recipe version may start in the past; past sales take it only when the
owner applies it, previewed, from the last count and at most 30 days back."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.daily_sales import save_day
from app.items import NewItem, create_item
from app.jobs import _handlers, drain
from app.ledger.posting import balance
from app.models import AuditEvent, StockItem
from app.products import NewVariation, create_product
from app.recipe_reprocess import apply, back_dated, preview, queue_apply
from app.recipes import NewLine, Owner, save_recipe
from app.transform.recipes import RecipeRuleError
from tests.ledger_helpers import counted_to

D = Decimal
NOW = datetime.now(UTC)
TODAY = NOW.date()  # the fixture location is in UTC with a midnight cutoff


@pytest.fixture
def bar(db, tenant, location):
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    juice = create_item(
        db,
        org,
        NewItem(
            name="Orange juice",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            quantity="50000",
        ),
    )
    size = create_product(db, org, name="Orange", variations=[NewVariation("16 oz")]).variations[0]
    db.commit()
    return org, juice, size


def _sold(db, org, location, size, *days):
    for day in days:
        save_day(db, org, location_id=location.id, day=day, quantities={size.id: D(1)})
    db.commit()


def _jobs(db):
    db.commit()
    drain(handlers={**_handlers(), "send_alert_digest": lambda *_: None})
    db.expire_all()


def _recipe(db, org, size, item, ml, starts=None, **kw):
    version = save_recipe(
        db,
        org,
        Owner("variation", size.id),
        [NewLine(item.id, str(ml), "ml")],
        effective_from=starts,
        **kw,
    )
    db.commit()
    return version


def test_only_a_size_starts_in_the_past_and_only_so_far(db, bar, location):
    org, juice, size = bar
    first = _recipe(db, org, size, juice, 300, NOW - timedelta(days=5))
    assert first.effective_from < first.created_at
    with pytest.raises(RecipeRuleError, match="up to 30 days back"):
        _recipe(db, org, size, juice, 320, NOW - timedelta(days=31))
    with pytest.raises(RecipeRuleError, match="after the version before it"):
        _recipe(db, org, size, juice, 320, NOW - timedelta(days=6))
    syrup = create_item(
        db,
        org,
        NewItem(
            name="Syrup",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            item_type="prepared",
        ),
    )
    with pytest.raises(RecipeRuleError, match="starts now or later"):
        save_recipe(
            db,
            org,
            Owner("item", syrup.id),
            [NewLine(juice.id, "100", "ml")],
            effective_from=NOW - timedelta(days=1),
        )


def test_a_first_recipe_dated_back_changes_nothing_until_applied(db, bar, location):
    """The owner's example: sold before the recipe existed, then a recipe dated back."""
    org, juice, size = bar
    _sold(db, org, location, size, TODAY - timedelta(days=5), TODAY - timedelta(days=3))
    assert balance(db, org, location.id, juice.id) == D(50000), "no recipe, nothing taken"

    version = _recipe(db, org, size, juice, 300, NOW - timedelta(days=7))
    _jobs(db)
    assert balance(db, org, location.id, juice.id) == D(50000), "saving applies nothing"
    assert back_dated(db, org, size).id == version.id

    shown = preview(db, org, size.id)
    assert (shown.sales, shown.units, shown.before_start) == (2, D(2), 0)
    assert shown.uses == (("Orange juice", "600 ml"),)
    queue_apply(db, org, size.id, shown.start)
    _jobs(db)
    assert balance(db, org, location.id, juice.id) == D(50000) - 600
    assert apply(db, org, version.id, shown.start) == 0, "a repeat reprocesses nothing"
    assert preview(db, org, size.id).sales == 0
    event = db.scalars(select(AuditEvent).where(AuditEvent.action == "recipe.applied_to_past"))
    assert event.one().after["sales"] == 2


def test_a_later_version_dated_back_keeps_counted_history(db, bar, location):
    org, juice, size = bar
    _recipe(db, org, size, juice, 200, from_the_start=True)
    _sold(db, org, location, size, TODAY - timedelta(days=5))  # 49,800 left
    stock = db.scalars(select(StockItem).where(StockItem.inventory_item_id == juice.id)).one()
    counted_on = TODAY - timedelta(days=4)
    counted_to(db, stock, 49700, at=datetime.combine(counted_on, time(12), UTC))
    _sold(db, org, location, size, TODAY - timedelta(days=3))  # 49,500 left

    _recipe(db, org, size, juice, 300, NOW - timedelta(days=6))
    shown = preview(db, org, size.id)
    assert shown.last_count == counted_on and shown.start == counted_on
    assert (shown.sales, shown.before_start) == (1, 1), "the sale before the count keeps its use"
    queue_apply(db, org, size.id, shown.start)
    _jobs(db)
    # The count stands; only the sale after it takes 300 instead of 200.
    assert balance(db, org, location.id, juice.id) == D(49400)


def test_seeded_and_current_recipes_are_not_back_dated(db, bar):
    org, juice, size = bar
    _recipe(db, org, size, juice, 200, from_the_start=True)
    assert back_dated(db, org, size) is None and preview(db, org, size.id) is None
    _recipe(db, org, size, juice, 250)
    assert back_dated(db, org, size) is None


def test_the_recipe_page_offers_and_applies(db, bar, location):
    from fastapi.testclient import TestClient

    from app.main import app

    org, juice, size = bar
    _sold(db, org, location, size, TODAY - timedelta(days=2))
    _recipe(db, org, size, juice, 300, NOW - timedelta(days=3))
    card = f"/app/recipes/variation/{size.id}"
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        assert "1 past sale still took what the earlier recipe said" in client.get(card).text
        page = client.get(card + "/apply").text
        assert "Apply to 1 sale" in page and "Orange juice: 300 ml" in page
        start = preview(db, org, size.id).start.isoformat()
        done = client.post(card + "/apply", data={"start": start})
        assert "Applying it to 1 past sale(s)" in done.text
    _jobs(db)
    assert balance(db, org, location.id, juice.id) == D(50000) - 300
