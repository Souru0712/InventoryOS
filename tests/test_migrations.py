"""Migrations must build exactly the schema the models describe, on SQLite and on Postgres."""

import os
from datetime import UTC
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

from app.db import Base, alembic_config

POSTGRES_URL = os.environ.get("TEST_DATABASE_URL", "")
if not POSTGRES_URL.startswith("postgresql"):
    POSTGRES_URL = ""


def _reset_postgres() -> None:
    """Empty the test database completely, enum types included."""
    engine = create_engine(POSTGRES_URL, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
def fresh_url(request, tmp_path: Path):
    """An empty database of each kind the app runs on. Postgres only when one is configured."""
    if request.param == "sqlite":
        yield f"sqlite:///{(tmp_path / 'migrations.db').as_posix()}"
        return
    if not POSTGRES_URL:
        pytest.skip("set TEST_DATABASE_URL to a Postgres database to check migrations there")
    _reset_postgres()
    try:
        yield POSTGRES_URL
    finally:
        _reset_postgres()


@pytest.fixture
def migrated(fresh_url):
    """A database built only by running the migrations."""
    command.upgrade(alembic_config(fresh_url), "head")
    engine = create_engine(fresh_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()


def test_there_is_exactly_one_head():
    scripts = ScriptDirectory.from_config(alembic_config())
    assert len(scripts.get_heads()) == 1, "branching migrations need a merge revision"


def test_migrations_match_the_models(migrated):
    """Catches a model change that nobody wrote a migration for."""
    with migrated.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "render_as_batch": True}
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"


def test_every_table_is_created(migrated):
    tables = set(inspect(migrated).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables


SNACK_BOX_TABLES = {
    "box_settings",
    "snack_profiles",
    "customers",
    "customer_login_tokens",
    "customer_product_preferences",
    "preference_changes",
    "box_orders",
    "box_lines",
    "box_reviews",
    "referrals",
    "credit_transactions",
}


def test_the_snack_box_tables_are_dropped(migrated):
    """DM-1 (T0.05): the module moved to its own repository; its tables and column go with it."""
    inspector = inspect(migrated)
    assert SNACK_BOX_TABLES.isdisjoint(inspector.get_table_names())
    assert "webhook_events" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("inventory_events")}
    assert "box_order_id" not in columns


def test_quantity_columns_survive_the_round_trip(migrated):
    """Qty must render as the decorator, not as one dialect's impl."""
    from decimal import Decimal

    from sqlalchemy.orm import Session

    from app.models import Location, StockItem, Tenant
    from app.units import Dimension

    with Session(migrated) as db:
        tenant = Tenant(name="Rosewood Coffee")
        db.add(tenant)
        db.flush()
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id="l1", name="Main"
        )
        db.add(location)
        db.flush()
        db.add(
            StockItem(
                tenant_id=tenant.id,
                location_id=location.id,
                slug="whole-milk",
                name="Whole milk",
                dimension=Dimension.VOLUME,
                stock_unit="l",
                qty_base=Decimal("34567.8901"),
                cost_per_base_cents=Decimal("0.0950"),
            )
        )
        db.commit()
        stored = db.query(StockItem).one()
        assert stored.qty_base == Decimal("34567.8901")
        assert stored.cost_per_base_cents == Decimal("0.0950")


def test_downgrade_removes_everything_and_the_schema_can_be_rebuilt(fresh_url):
    """Rebuilding afterwards proves Postgres enum types went too, not just the tables."""
    config = alembic_config(fresh_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    engine = create_engine(fresh_url, poolclass=NullPool)
    try:
        remaining = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert remaining == set()
    finally:
        engine.dispose()

    command.upgrade(config, "head")


def test_every_migration_survives_a_database_that_already_has_rows(fresh_url):
    """Upgrade one revision at a time with data present, not just onto an empty schema.

    A column added NOT NULL with no default builds fine on an empty table and fails on a
    populated one, which is the only kind a customer has.
    """
    from datetime import date, datetime

    config = alembic_config(fresh_url)
    scripts = ScriptDirectory.from_config(config)
    revisions = list(reversed(list(scripts.walk_revisions())))
    engine = create_engine(fresh_url, poolclass=NullPool)
    now = datetime.now(UTC)
    photo = "ab" * 32

    # Rows to plant as soon as the table they belong to exists, so later revisions
    # have to migrate real data rather than an empty schema.
    seeds = {
        "tenants": {
            "name": "Rosewood",
            "plan": "TRIAL",
            "report_day": 0,
            "report_hour": 7,
            "timezone": "UTC",
            "created_at": now,
        },
        "locations": {
            "tenant_id": 1,
            "provider": "square",
            "provider_location_id": "L1",
            "name": "Main",
            "timezone": "UTC",
            "currency": "USD",
            "is_active": True,
        },
        "stock_items": {
            "tenant_id": 1,
            "location_id": 1,
            "slug": "milk",
            "name": "Milk",
            "dimension": "VOLUME",
            "stock_unit": "l",
            "track_mode": "MANUAL",
            "qty_base": "10.0000",
            "reorder_point_base": "0.0000",
            "cost_per_base_cents": "0.0950",
            "lead_time_days": 2,
            "receive_unit": "l",
            "is_active": True,
        },
        # Saved the way uploads were before storage keys: a path under the upload directory.
        "documents": {
            "tenant_id": 1,
            "location_id": 1,
            "kind": "invoice",
            "currency": "USD",
            "file_path": f"var/uploads/1/{photo}.jpg",
            "content_type": "image/jpeg",
            "sha256": photo,
            "byte_size": 12,
            "status": "APPLIED",
            "uploaded_at": now,
        },
        "stock_batches": {
            "tenant_id": 1,
            "stock_item_id": 1,
            "received_qty_base": "10.0000",
            "expires_on": date(2026, 12, 1),
            "source": "label",
            "received_at": now,
        },
    }
    planted: set[str] = set()

    try:
        for revision in revisions:
            command.upgrade(config, revision.revision)
            tables = set(inspect(engine).get_table_names())
            with engine.begin() as connection:
                for table, values in seeds.items():
                    if table in planted or table not in tables:
                        continue
                    columns = ", ".join(values)
                    binds = ", ".join(f":{name}" for name in values)
                    # Table and column names come from this test's own seed data.
                    sql = f"INSERT INTO {table} ({columns}) VALUES ({binds})"  # noqa: S608
                    connection.execute(text(sql), values)
                    planted.add(table)

        assert planted == set(seeds), f"never planted: {set(seeds) - planted}"
        with engine.connect() as connection:
            kept = connection.execute(text("SELECT count(*) FROM stock_batches")).scalar()
            key = connection.execute(text("SELECT storage_key FROM documents")).scalar()
        assert kept == 1, "a migration dropped rows that were already there"
        assert key == f"1/{photo}.jpg", "an old upload path was not turned into a storage key"
    finally:
        engine.dispose()
