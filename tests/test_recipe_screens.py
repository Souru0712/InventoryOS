"""T2.02-b: the recipe editor and staff card (RCP-FR-02, -05..09, -14, AC-RCP-01, -06, -09)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.items import NewItem, create_item
from app.main import app
from app.models import Membership, RecipeVersion, User
from app.products import NewVariation, create_product
from app.security import hash_password

PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)
MONEY = re.compile(r"\$\s?\d")


@pytest.fixture
def kitchen(db, tenant, location):
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
                cost_per_unit="2.00",
            ),
        )

    made = {
        "milk": item("Milk", "volume", "ml"),
        "beans": item("Espresso beans", "weight", "g"),
        "cup": item("Cup 16 oz", "count", "each", "packaging"),
        "ginger": item("Ginger", "weight", "g"),
        "juice": item("Ginger juice", "volume", "ml", "prepared"),
    }
    golden = create_product(
        db,
        org,
        name="Golden State",
        variations=[NewVariation("12 oz", "6.50"), NewVariation("16 oz", "7.50")],
    )
    db.commit()
    return made, golden


def member(db, tenant, role):
    user = User(
        tenant_id=tenant.id, email=f"{role}@rosewood.test", password_hash=hash_password(PASSWORD)
    )
    db.add(user)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
    db.commit()


def signed_in(email="owner@rosewood.test") -> TestClient:
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": email, "password": PASSWORD})
    return client


def rows(*lines, instructions="", yield_quantity="1", **extra):
    data = {
        "item": [str(i) for i, _, _ in lines],
        "quantity": [q for _, q, _ in lines],
        "unit": [u for _, _, u in lines],
        "instructions": instructions,
        "yield_quantity": yield_quantity,
    }
    data.update(extra)
    return data


# --- AC-RCP-01 on screen -----------------------------------------------------------------------


def test_ac_rcp_01_a_manager_writes_a_16_oz_recipe_in_mixed_units(db, tenant, kitchen):
    item, golden = kitchen
    large = golden.variations[1]
    member(db, tenant, "manager")
    client = signed_in("manager@rosewood.test")
    editor = client.get(f"/app/recipes/variation/{large.id}/edit")
    assert editor.status_code == 200 and "Golden State 16 oz" in editor.text

    refused = client.post(
        f"/app/recipes/variation/{large.id}",
        data=rows((item["milk"].id, "250", "ml"), (item["ginger"].id, "120", "ml")),
    )
    assert refused.status_code == 400
    assert "Ginger" in refused.text and 'value="120"' in refused.text  # the conversion is named
    assert db.scalar(select(func.count()).select_from(RecipeVersion)) == 0

    saved = client.post(
        f"/app/recipes/variation/{large.id}",
        data=rows(
            (item["milk"].id, "0.25", "l"),
            (item["beans"].id, "18", "g"),
            (item["cup"].id, "1", ""),
            instructions="Shake hard.\nPour over ice.",
        ),
    )
    assert "Saved version 1. Earlier sales keep their version." in saved.text
    card = saved.text
    assert "Milk" in card and "0.25 L" in card and "18 g" in card
    assert "packaging" in card and "Pour over ice." in card
    assert "makes 1 serving" in card


def test_an_unchanged_save_adds_no_version_and_an_edit_shows_what_changed(db, tenant, kitchen):
    item, golden = kitchen
    small = golden.variations[0]
    client = signed_in()
    path = f"/app/recipes/variation/{small.id}"
    client.post(path, data=rows((item["milk"].id, "200", "ml")))
    again = client.post(path, data=rows((item["milk"].id, "200", "ml")))
    assert "Nothing changed, so no new version." in again.text
    later = client.post(path, data=rows((item["milk"].id, "240", "ml")))
    assert "Saved version 2" in later.text
    assert "Milk 200 ml to 240 ml" in later.text and "First version" in later.text
    assert "owner@rosewood.test" in later.text  # who saved it (RCP-FR-08)


def test_a_version_can_start_later_and_the_card_says_when(db, tenant, kitchen):
    item, golden = kitchen
    small = golden.variations[0]
    client = signed_in()
    path = f"/app/recipes/variation/{small.id}"
    client.post(path, data=rows((item["milk"].id, "200", "ml")))
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    page = client.post(path, data=rows((item["milk"].id, "240", "ml"), starts_at=tomorrow)).text
    assert "it starts at the time you chose" in page
    assert "Version 2 starts" in page and "200 ml" in page  # version 1 still in use
    past = client.post(
        path, data=rows((item["milk"].id, "300", "ml"), starts_at="2020-01-01T09:00")
    )
    assert past.status_code == 400 and "the past keeps its recipe" in past.text


# --- AC-RCP-06 on screen -----------------------------------------------------------------------


def test_ac_rcp_06_start_from_12_oz_fills_the_16_oz_editor_scaled(db, tenant, kitchen):
    item, golden = kitchen
    small, large = golden.variations
    client = signed_in()
    client.post(
        f"/app/recipes/variation/{small.id}",
        data=rows(
            (item["juice"].id, "120", "ml"),
            (item["beans"].id, "9", "g"),
            (item["cup"].id, "1", ""),
            instructions="Shake, then pour.",
        ),
    )
    editor = client.get(f"/app/recipes/variation/{large.id}/edit")
    assert f"/app/recipes/variation/{large.id}/edit?start={small.id}" in editor.text
    filled = client.get(f"/app/recipes/variation/{large.id}/edit?start={small.id}").text
    assert "Filled from 12 oz, scaled by size." in filled
    assert 'value="160"' in filled and 'value="12"' in filled
    assert "1.3333" not in filled  # one cup still: packaging is not scaled
    assert "Shake, then pour." in filled  # the instructions come along


# --- AC-RCP-09: Employees read cards, never costs ----------------------------------------------


def test_ac_rcp_09_an_employee_reads_the_card_with_no_cost_and_cannot_edit(db, tenant, kitchen):
    item, golden = kitchen
    small = golden.variations[0]
    signed_in().post(
        f"/app/recipes/variation/{small.id}",
        data=rows((item["milk"].id, "200", "ml"), instructions="Steam to 60C."),
    )
    member(db, tenant, "employee")
    client = signed_in("employee@rosewood.test")
    card = client.get(f"/app/recipes/variation/{small.id}")
    assert card.status_code == 200
    assert "Milk" in card.text and "Steam to 60C." in card.text
    assert not MONEY.search(card.text) and "cost" not in card.text.lower()
    assert "Edit recipe" not in card.text
    assert client.get(f"/app/recipes/variation/{small.id}/edit").status_code == 403
    assert client.post(f"/app/recipes/variation/{small.id}", data={}).status_code == 403


# --- production recipes and the way in ---------------------------------------------------------


def test_a_prepared_item_has_a_production_recipe_with_its_yield(db, tenant, kitchen):
    item, golden = kitchen
    client = signed_in()
    page = client.post(
        f"/app/recipes/item/{item['juice'].id}",
        data=rows((item["ginger"].id, "450", "g"), yield_quantity="0.5", yield_unit="l"),
    ).text
    assert "Production recipe" in page and "makes 0.5 l" in page
    assert client.get(f"/app/recipes/item/{item['milk'].id}").status_code == 404  # raw: none
    links = client.get(f"/app/catalog/{item['juice'].id}").text
    assert f"/app/recipes/item/{item['juice'].id}" in links
    product = client.get(f"/app/products/{golden.id}").text
    assert f"/app/recipes/variation/{golden.variations[0].id}" in product
