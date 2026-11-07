"""T1.02 (C-33): every product query runs inside one organization, by construction."""

import ast
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.access import OrgContext, UnscopedAccess, get_scoped, select_scoped, update_scoped
from app.access.system import connection_for_merchant, user_by_email
from app.models import (
    EmailSuppression,
    InventoryCount,
    Location,
    Membership,
    ProviderConnection,
    SoldItem,
    StockItem,
    Tenant,
    User,
)
from app.units import Dimension

ROOT = Path(__file__).resolve().parent.parent
SCANNED = ("app/web", "app/orchestration")
RAW_SQL_NAMES = {"select", "update", "delete", "insert", "text"}
SESSIONS = {"db", "session"}


def unscoped_queries(source: str, filename: str = "<source>") -> list[str]:
    """Raw statements and lookups by id that bypass app.access, as file:line descriptions."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sqlalchemy"):
            for alias in node.names:
                if alias.name in RAW_SQL_NAMES:
                    found.append(f"{filename}:{node.lineno} imports sqlalchemy.{alias.name}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlalchemy":
                    found.append(f"{filename}:{node.lineno} imports sqlalchemy")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if node.func.attr == "get" and isinstance(owner, ast.Name) and owner.id in SESSIONS:
                found.append(f"{filename}:{node.lineno} {owner.id}.get()")
            if node.func.attr == "query":
                found.append(f"{filename}:{node.lineno} .query()")
    return found


def test_routes_and_services_never_query_outside_the_access_layer():
    files = [path for folder in SCANNED for path in sorted((ROOT / folder).glob("*.py"))]
    assert len(files) > 20, "the scan found too few files to mean anything"
    found = [
        problem
        for path in files
        for problem in unscoped_queries(
            path.read_text(encoding="utf-8"), str(path.relative_to(ROOT))
        )
    ]
    assert found == [], "use app.access (select_scoped, get_scoped) or app.access.system"


def test_the_scan_catches_what_routes_used_to_do():
    """The pre-T1.02 pattern, so the test above cannot pass by finding nothing."""
    before = """
from sqlalchemy import select, update
from sqlalchemy.sql import text

def page(db, item_id, tenant):
    item = db.get(StockItem, item_id)
    rows = db.scalars(select(StockItem).where(StockItem.tenant_id == tenant.id))
    db.execute(update(Promotion).values(stock_batch_id=None))
    db.execute(text("SELECT 1"))
    return session.get(Location, 1), db.query(StockItem).all()
"""
    found = unscoped_queries(before)
    assert len(found) == 6, found


# --- the helpers --------------------------------------------------------------------------------


def _org_with_items(db, name: str, locations: int = 1) -> tuple[Tenant, list[Location]]:
    tenant = Tenant(name=name)
    db.add(tenant)
    db.flush()
    places = []
    for number in range(locations):
        place = Location(
            tenant_id=tenant.id,
            provider="demo",
            provider_location_id=f"l{number}",
            name=f"L{number}",
        )
        db.add(place)
        db.flush()
        db.add(
            StockItem(
                tenant_id=tenant.id,
                location_id=place.id,
                slug=f"milk-{number}",
                name=f"Milk {number}",
                dimension=Dimension.COUNT,
                stock_unit="each",
            )
        )
        places.append(place)
    db.flush()
    return tenant, places


def test_a_scoped_select_sees_only_its_organization(db):
    mine, _ = _org_with_items(db, "Mine", locations=2)
    theirs, _ = _org_with_items(db, "Theirs")
    org = OrgContext.system(mine.id)

    names = sorted(item.name for item in db.scalars(select_scoped(org, StockItem)))
    assert names == ["Milk 0", "Milk 1"]
    assert [t.id for t in db.scalars(select_scoped(org, Tenant))] == [mine.id]
    assert theirs.id not in {loc.tenant_id for loc in db.scalars(select_scoped(org, Location))}


def test_a_scoped_get_does_not_find_another_organizations_row(db):
    mine, _ = _org_with_items(db, "Mine")
    theirs, _ = _org_with_items(db, "Theirs")
    org = OrgContext.system(mine.id)
    their_item = db.scalars(select_scoped(OrgContext.system(theirs.id), StockItem)).one()

    assert get_scoped(db, org, StockItem, their_item.id) is None
    assert get_scoped(db, org, Tenant, theirs.id) is None
    assert get_scoped(db, org, Tenant, mine.id) is mine
    assert get_scoped(db, org, StockItem, 999_999) is None


def test_a_scoped_update_changes_only_its_organization(db):
    mine, _ = _org_with_items(db, "Mine")
    theirs, _ = _org_with_items(db, "Theirs")
    db.execute(update_scoped(OrgContext.system(mine.id), StockItem).values(qty_base=Decimal("7")))
    db.flush()
    db.expire_all()
    # The test itself looks across organizations, to see both.
    quantities = {item.tenant_id: item.qty_base for item in db.scalars(select(StockItem))}
    assert quantities == {mine.id: Decimal("7"), theirs.id: Decimal("0")}


def test_a_location_limited_context_narrows_location_rows_only(db):
    mine, (first, second) = _org_with_items(db, "Mine", locations=2)
    db.add(SoldItem(tenant_id=mine.id, provider="demo", provider_item_id="latte", name="Latte"))
    db.flush()
    org = OrgContext(tenant_id=mine.id, location_ids=frozenset({second.id}))

    assert [item.location_id for item in db.scalars(select_scoped(org, StockItem))] == [second.id]
    assert [loc.id for loc in db.scalars(select_scoped(org, Location))] == [second.id]
    assert get_scoped(db, org, Location, first.id) is None
    # An organization-wide row is not narrowed by location.
    assert db.scalar(select(func.count()).select_from(select_scoped(org, SoldItem).subquery())) == 1
    # A row whose location is known only through its item is refused, not shown unfiltered.
    with pytest.raises(UnscopedAccess, match="InventoryCount"):
        select_scoped(org, InventoryCount)


@pytest.mark.parametrize("model", [EmailSuppression, User])
def test_tables_that_belong_to_no_organization_are_refused(model):
    """Users are global identities (SAAS-T-03): a business reaches them through memberships."""
    with pytest.raises(UnscopedAccess, match="app.access.system"):
        select_scoped(OrgContext.system(1), model)


def test_a_context_is_built_from_a_membership_and_cannot_be_changed(db, tenant):
    membership = db.scalars(select(Membership).where(Membership.tenant_id == tenant.id)).one()
    org = OrgContext.for_membership(membership)
    assert (org.tenant_id, org.user_id, org.location_ids) == (
        tenant.id,
        membership.user_id,
        None,
    )
    with pytest.raises(FrozenInstanceError):
        org.tenant_id = tenant.id + 1  # type: ignore[misc]
    assert OrgContext.system(tenant.id) == OrgContext(tenant_id=tenant.id)


# --- the named lookups that cross organizations ---------------------------------------------------


def test_sign_in_finds_the_account_by_email_whatever_its_case(db, tenant):
    assert user_by_email(db, "  Owner@Rosewood.TEST ").tenant_id == tenant.id
    assert user_by_email(db, "nobody@rosewood.test") is None


def test_a_webhook_finds_only_an_active_connection(db, tenant):
    other = Tenant(name="Other")
    db.add(other)
    db.flush()
    db.add_all(
        [
            ProviderConnection(
                tenant_id=tenant.id, merchant_id="M1", access_token_enc="x", status="active"
            ),
            ProviderConnection(
                tenant_id=other.id, merchant_id="M2", access_token_enc="x", status="revoked"
            ),
        ]
    )
    db.flush()
    assert connection_for_merchant(db, "square", "M1").tenant_id == tenant.id
    assert connection_for_merchant(db, "square", "M2") is None
