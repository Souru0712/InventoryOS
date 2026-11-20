"""T3.04: product mapping and reprocessing (POS-FR-06, POS-FR-14, PD-17, WF-28, AC-POS-06)."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.consumption import SOURCE
from app.db import Base, SessionLocal, engine
from app.items import NewItem, create_item
from app.jobs import _handlers, drain
from app.ledger.posting import balance, post_count, standing
from app.main import app
from app.mappings import (
    MappingRuleError,
    NotMapped,
    create_and_map,
    map_modifier,
    map_product,
    preview,
    product_rows,
    queue_reprocess,
    reprocess,
)
from app.models import (
    AuditEvent,
    ExceptionRecord,
    ExternalModifier,
    ExternalProduct,
    Job,
    Location,
    ProductMapping,
    ProviderConnection,
    SoldItem,
    Tenant,
)
from app.products import NewVariation, create_product, list_products, save_modifier
from app.recipes import NewLine, Owner, save_recipe
from app.sales import DayOfSales, add_source, record_daily_pos_sales, record_sale
from app.transform.mappings import Candidate, check_start, default_start, suggest
from app.transform.sales import SaleLine
from tests.accounts import confirm_email

D = Decimal


# --- the rules (pure) ----------------------------------------------------------------------------


def test_suggestions_match_the_product_then_the_size():
    candidates = [
        Candidate(1, "Latte", "12 oz"),
        Candidate(2, "Latte", "16 oz"),
        Candidate(3, "Iced latte", "16 oz"),
        Candidate(4, "Cold brew", "Regular"),
        Candidate(5, "Muffin", "Regular"),
    ]
    assert suggest("Latte", "16 oz", candidates) == [2, 1, 3]
    assert suggest("COLD BREW", "", candidates) == [4], "no size means the regular one"
    assert suggest("Bagel", "", candidates) == []


def test_reprocessing_starts_at_the_last_count_and_reaches_back_30_days_at_most():
    today = date(2026, 11, 19)
    assert default_start(today, None) == date(2026, 10, 20)
    assert default_start(today, date(2026, 11, 16)) == date(2026, 11, 16)
    assert default_start(today, date(2026, 9, 1)) == date(2026, 10, 20)
    with pytest.raises(MappingRuleError, match="30 days"):
        check_start(date(2026, 10, 19), today)
    with pytest.raises(MappingRuleError, match="future"):
        check_start(date(2026, 11, 20), today)


# --- mapping and reprocessing ---------------------------------------------------------------------


@pytest.fixture
def shop(db, tenant, location):
    """A connected business: Square's latte and modifier, its own latte and flat white with
    recipes, milk on the shelf, and a Square sales feed at its location."""
    location.provider, location.provider_location_id = "square", "SQ-1"
    org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
    connection = ProviderConnection(
        tenant_id=tenant.id, provider="square", merchant_id="M-1", access_token_enc="enc"
    )
    db.add(connection)
    db.flush()
    pos_latte = ExternalProduct(
        tenant_id=tenant.id,
        connection_id=connection.id,
        external_item_id="ITEM-LATTE",
        external_variation_id="VAR-LATTE",
        name="Latte",
        variation_name="12 oz",
    )
    pos_ice = ExternalModifier(
        tenant_id=tenant.id,
        connection_id=connection.id,
        external_modifier_id="MOD-ICE",
        list_name="Ice",
        name="Light ice",
    )
    db.add_all([pos_latte, pos_ice])
    milk = create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            cost_per_unit="0.01",
        ),
    )
    latte = create_product(db, org, name="Latte", variations=[NewVariation("12 oz")])
    flat = create_product(db, org, name="Flat white", variations=[NewVariation("8 oz")])
    save_recipe(
        db, org, Owner("variation", latte.variations[0].id), [NewLine(milk.id, "200", "ml")]
    )
    save_recipe(db, org, Owner("variation", flat.variations[0].id), [NewLine(milk.id, "150", "ml")])
    ice = save_modifier(db, org, name="Light ice", group="", effect="scale", scale_factor="1.15")
    feed = add_source(
        db,
        org,
        location_id=location.id,
        kind="pos_adapter",
        provider="square",
        active_from=date(2026, 1, 1),
    )
    db.commit()
    return org, pos_latte, pos_ice, latte.variations[0], flat.variations[0], ice, milk, feed


def _jobs():
    return drain(handlers={**_handlers(), "send_alert_digest": lambda *_: None})


def _sell(db, org, feed, reference, qty, at=None):
    sale = record_sale(
        db,
        org,
        feed,
        source_reference=reference,
        occurred_at=at or datetime.now(UTC),
        lines=[SaleLine("1", D(qty), external_ref="VAR-LATTE", description="Latte - 12 oz")],
    )
    db.commit()
    _jobs()
    db.expire_all()
    return sale


def _unmapped(db) -> list[ExceptionRecord]:
    return list(
        db.scalars(
            select(ExceptionRecord).where(
                ExceptionRecord.type == "unmapped_product",
                ExceptionRecord.subject_type == "pos_item",
            )
        )
    )


def test_ac_pos_06_mapping_then_reprocessing_posts_once_at_the_original_moment(db, location, shop):
    org, pos_latte, _ice, latte, _flat, _modifier, milk, feed = shop
    at = datetime.now(UTC)
    sale = _sell(db, org, feed, "o-1", 2, at)
    assert balance(db, org, location.id, milk.id) == 0, "an unmapped sale uses nothing"
    [problem] = _unmapped(db)
    assert problem.status == "open"
    with pytest.raises(NotMapped):
        preview(db, org, pos_latte.id)

    assert map_product(db, org, pos_latte.id, latte.id) is True
    db.commit()
    assert _unmapped(db)[0].status == "resolved", "mapping fixes the cause"
    shown = preview(db, org, pos_latte.id)
    assert (shown.sales, shown.units, shown.last_count) == (1, D(2), None)
    assert shown.start == shown.today - timedelta(days=30)
    assert shown.uses == (("Milk", "400 ml"),)
    assert balance(db, org, location.id, milk.id) == 0, "a preview changes nothing"

    queue_reprocess(db, org, pos_latte.id, shown.start)
    db.commit()
    _jobs()
    db.expire_all()
    assert balance(db, org, location.id, milk.id) == D(-400)
    posted = [t for t in standing(db, org, SOURCE, str(sale.id)) if t.kind == "sale"]
    assert [t.occurred_at for t in posted] == [at]
    assert preview(db, org, pos_latte.id).sales == 0

    # Again, by hand or by the queue: nothing more leaves stock.
    mapping = db.scalars(select(ProductMapping).where(ProductMapping.status == "active")).one()
    assert reprocess(db, org, mapping.id, shown.start) == 0
    db.commit()
    assert balance(db, org, location.id, milk.id) == D(-400)
    reprocessed = [e for e in db.scalars(select(AuditEvent)) if e.action == "mapping.reprocessed"]
    assert reprocessed[0].after["sales"] == 1


def test_a_remap_gives_back_the_old_size_and_posts_the_new(db, location, shop):
    org, pos_latte, _ice, latte, flat, _modifier, milk, feed = shop
    _sell(db, org, feed, "o-1", 2)
    map_product(db, org, pos_latte.id, latte.id)
    queue_reprocess(db, org, pos_latte.id, preview(db, org, pos_latte.id).start)
    db.commit()
    _jobs()

    map_product(db, org, pos_latte.id, flat.id)
    shown = preview(db, org, pos_latte.id)
    assert (shown.sales, shown.uses) == (1, (("Milk", "300 ml"),))
    queue_reprocess(db, org, pos_latte.id, shown.start)
    db.commit()
    _jobs()
    db.expire_all()
    assert balance(db, org, location.id, milk.id) == D(-300)
    history = list(db.scalars(select(ProductMapping).order_by(ProductMapping.id)))
    assert [(m.product_variation_id, m.status) for m in history] == [
        (latte.id, "superseded"),
        (flat.id, "active"),
    ]
    assert history[0].superseded_at is not None


def test_pd_17_reprocessing_starts_at_the_last_count_of_what_the_size_uses(db, location, shop):
    org, pos_latte, _ice, latte, _flat, _modifier, milk, feed = shop
    counted = datetime.now(UTC) - timedelta(days=3)
    post_count(
        db,
        org,
        location_id=location.id,
        inventory_item_id=milk.id,
        counted=D(5000),
        source_type="test",
        source_id="c1",
        idempotency_key="count:test:c1",
        occurred_at=counted,
    )
    _sell(db, org, feed, "o-1", 1)
    map_product(db, org, pos_latte.id, latte.id)
    shown = preview(db, org, pos_latte.id)
    assert shown.last_count == counted.date() and shown.start == counted.date()
    assert shown.sales == 1
    later = preview(db, org, pos_latte.id, shown.today)
    assert later.start == shown.today


def test_new_pos_sales_use_the_mapping_without_reprocessing(db, tenant, location, shop):
    org, pos_latte, _ice, latte, _flat, _modifier, _milk, _feed = shop
    map_product(db, org, pos_latte.id, latte.id)
    sold = SoldItem(tenant_id=tenant.id, provider="square", provider_item_id="VAR-LATTE", name="L")
    db.add(sold)
    db.flush()
    day = datetime.now(UTC).date()
    record_daily_pos_sales(db, org, "square", [DayOfSales(location.id, day, sold, D(3))])
    db.commit()
    rows = product_rows(db, org, db.get(ProviderConnection, pos_latte.connection_id))
    assert rows[0].unmapped_units == 0


def test_mapping_history_unmapping_and_modifiers_are_audited(db, shop):
    org, pos_latte, pos_ice, latte, _flat, ice, _milk, _feed = shop
    assert map_product(db, org, pos_latte.id, latte.id) is True
    assert map_product(db, org, pos_latte.id, latte.id) is False
    assert map_product(db, org, pos_latte.id, None) is True
    assert map_product(db, org, pos_latte.id, None) is False
    assert map_modifier(db, org, pos_ice.id, ice.id) is True
    assert map_modifier(db, org, pos_ice.id, None) is True
    db.commit()
    assert not db.scalars(select(ProductMapping).where(ProductMapping.status == "active")).all()
    summaries = [e.summary for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id))]
    assert summaries[-4:] == [
        "Mapped Latte 12 oz to Latte 12 oz",
        "Unmapped Latte 12 oz",
        "Mapped Ice: Light ice to Light ice",
        "Unmapped Ice: Light ice",
    ]


def test_a_pos_product_can_become_one_of_the_business_products(db, tenant, shop):
    org, pos_latte, _ice, latte, _flat, _modifier, _milk, _feed = shop
    connection_id = pos_latte.connection_id
    brew = ExternalProduct(
        tenant_id=tenant.id,
        connection_id=connection_id,
        external_variation_id="VAR-BREW",
        name="Cold brew",
        variation_name="",
        category="Coffee",
    )
    large = ExternalProduct(
        tenant_id=tenant.id,
        connection_id=connection_id,
        external_variation_id="VAR-LATTE-16",
        name="Latte",
        variation_name="16 oz",
    )
    db.add_all([brew, large])
    db.flush()
    made = create_and_map(db, org, brew.id)
    assert (made.product.name, made.name, made.product.category) == (
        "Cold brew",
        "Regular",
        "Coffee",
    )
    added = create_and_map(db, org, large.id)
    assert added.product_id == latte.product_id and added.name == "16 oz"
    assert len([p for p in list_products(db, org) if p.name == "Latte"]) == 1
    rows = {
        r.external.id: r for r in product_rows(db, org, db.get(ProviderConnection, connection_id))
    }
    assert rows[brew.id].mapped.id == made.id
    assert rows[pos_latte.id].suggestions[0] == latte.id


def test_a_size_no_longer_sold_cannot_be_mapped_to(db, shop):
    org, pos_latte, _ice, latte, *_rest = shop
    latte.is_active = False
    with pytest.raises(MappingRuleError, match="no longer sold"):
        map_product(db, org, pos_latte.id, latte.id)


# --- the screens ---------------------------------------------------------------------------------


@pytest.fixture
def owner():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client = TestClient(app)
    client.__enter__()
    client.post(
        "/signup",
        data={
            "business_name": "Corner Cafe",
            "email": "owner@corner.test",
            "password": "a-good-password",
        },
    )
    confirm_email("owner@corner.test")
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        org = OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))
        place = Location(
            tenant_id=tenant.id,
            name="Main",
            timezone="UTC",
            provider="square",
            provider_location_id="SQ-1",
        )
        connection = ProviderConnection(
            tenant_id=tenant.id, provider="square", merchant_id="M-1", access_token_enc="enc"
        )
        db.add_all([place, connection])
        db.flush()
        db.add(
            ExternalProduct(
                tenant_id=tenant.id,
                connection_id=connection.id,
                external_variation_id="VAR-LATTE",
                name="Latte",
                variation_name="12 oz",
            )
        )
        create_product(db, org, name="Latte", variations=[NewVariation("12 oz")])
        feed = add_source(
            db,
            org,
            location_id=place.id,
            kind="pos_adapter",
            provider="square",
            active_from=date(2026, 1, 1),
        )
        record_sale(
            db,
            org,
            feed,
            source_reference="o-1",
            occurred_at=datetime.now(UTC),
            lines=[SaleLine("1", D(2), external_ref="VAR-LATTE", description="Latte")],
        )
        db.commit()
    yield client
    client.__exit__(None, None, None)


def test_the_mapping_screen_suggests_maps_and_offers_to_reprocess(owner):
    page = owner.get("/app/mappings").text
    assert "Suggested" in page and "Latte &mdash; 12 oz" in page
    with SessionLocal() as db:
        pos = db.scalars(select(ExternalProduct)).one()
        size = list_products(db, OrgContext.system(pos.tenant_id))[0].variations[0]
    mapped = owner.post(
        f"/app/mappings/products/{pos.id}", data={"target": size.id}, follow_redirects=False
    )
    assert mapped.headers["location"] == f"/app/mappings/products/{pos.id}/reprocess"
    review = owner.get(mapped.headers["location"]).text
    assert "1 sale</strong>" in review and "Reprocess 1 sale" in review
    start = re.search(r'name="start" value="([\d-]+)"', review).group(1)
    done = owner.post(f"/app/mappings/products/{pos.id}/reprocess", data={"start": start})
    assert "Reprocessing 1 sale(s) in the background." in done.text
    with SessionLocal() as db:
        assert db.scalars(select(Job).where(Job.kind == "reprocess_mapping")).one()
    too_early = owner.get(
        f"/app/mappings/products/{pos.id}/reprocess", params={"start": "2000-01-01"}
    )
    assert "30 days at most" in too_early.text


# --- the rules -----------------------------------------------------------------------------------


def test_only_the_mapping_service_writes_mappings():
    writes = re.compile(r"ProductMapping\(|update_scoped\(\w+, ProductMapping")
    offenders = [
        path.as_posix()
        for path in Path("app").rglob("*.py")
        if path.as_posix() not in {"app/mappings.py", "app/models.py"}
        and writes.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
