"""Migrations must build exactly the schema the models describe, on SQLite and on Postgres."""

import os
from datetime import UTC
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, create_engine, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.db import Base, UTCDateTime, alembic_config

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


def test_timestamps_checks_and_delete_rules_are_migrated(migrated):
    """DM-2 (T1.01): what the models declare, a migrated database enforces."""
    inspector = inspect(migrated)
    postgres = migrated.dialect.name == "postgresql"
    for table in Base.metadata.sorted_tables:
        if postgres:
            types = {c["name"]: c["type"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if isinstance(column.type, UTCDateTime):
                    assert types[column.name].timezone, f"{table.name}.{column.name} is naive"

        declared = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        found = {c["name"] for c in inspector.get_check_constraints(table.name)}
        assert declared <= found, f"{table.name} lacks {declared - found}"

        rules = {(fk.parent.name, fk.ondelete) for fk in table.foreign_keys}
        found_rules = {
            (fk["constrained_columns"][0], fk["options"].get("ondelete"))
            for fk in inspector.get_foreign_keys(table.name)
        }
        assert found_rules == rules, table.name


def test_dm3_a_location_needs_no_pos_and_the_downgrade_gives_it_ids(fresh_url):
    """DM-3 (T1.04): owner-added locations carry no provider ids; rolling back names them."""
    config = alembic_config(fresh_url)
    command.upgrade(config, "head")
    engine = create_engine(fresh_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants (name, plan, subscription_status, report_day, report_hour,"
                    " timezone, created_at) VALUES ('Corner', 'PRO', 'TRIALING', 0, 7, 'UTC',"
                    " '2026-11-06 12:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO locations (tenant_id, name, timezone, currency, is_active)"
                    " VALUES (1, 'Main', 'America/Los_Angeles', 'USD', true)"
                )
            )
            row = connection.execute(
                text("SELECT business_day_cutoff_hour, open_days FROM locations")
            ).one()
        assert tuple(row) == (0, 127), "server defaults give a midnight day, open every day"

        command.downgrade(config, "68c6fdbc31a7")
        with engine.connect() as connection:
            ids = connection.execute(
                text("SELECT provider, provider_location_id FROM locations")
            ).one()
        assert tuple(ids) == ("manual", "manual-1")
    finally:
        engine.dispose()


def test_dm4_every_existing_user_becomes_the_owner_of_its_business(fresh_url):
    """DM-4 (T1.05): one active Owner membership per former user, in its former tenant."""
    config = alembic_config(fresh_url)
    command.upgrade(config, "912d17673f03")
    engine = create_engine(fresh_url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            for name in ("Rosewood", "Juniper"):
                connection.execute(
                    text(
                        "INSERT INTO tenants (name, plan, subscription_status, report_day,"
                        " report_hour, timezone, created_at) VALUES (:name, 'PRO', 'TRIALING',"
                        " 0, 7, 'UTC', '2026-11-06 12:00:00')"
                    ),
                    {"name": name},
                )
            for tenant_id, email in ((1, "a@rosewood.test"), (2, "b@juniper.test")):
                connection.execute(
                    text(
                        "INSERT INTO users (tenant_id, email, password_hash, is_active,"
                        " created_at, wants_stock_alerts, wants_expiry_alerts,"
                        " wants_weekly_report) VALUES (:tenant_id, :email, 'x', true,"
                        " '2026-11-06 12:00:00', true, true, true)"
                    ),
                    {"tenant_id": tenant_id, "email": email},
                )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT tenant_id, user_id, role, status, all_locations FROM memberships"
                    " ORDER BY user_id"
                )
            ).all()
        assert [tuple(row) for row in rows] == [
            (1, 1, "owner", "active", True),
            (2, 2, "owner", "active", True),
        ]
    finally:
        engine.dispose()


BEFORE_DM5 = "d8a2b6e4f1c3"


def test_dm5_every_stock_item_maps_to_exactly_one_item_and_location(fresh_url):
    """DM-5 (T1.13-a): one item per slug and dimension per business; one setting per row."""
    config = alembic_config(fresh_url)
    command.upgrade(config, BEFORE_DM5)
    engine = create_engine(fresh_url, poolclass=NullPool)
    stock = (
        "INSERT INTO stock_items (tenant_id, location_id, slug, name, category, dimension,"
        " stock_unit, track_mode, qty_base, reorder_point_base, par_level_base,"
        " cost_per_base_cents, retail_per_base_cents, lead_time_days, receive_unit,"
        " tracks_expiry, is_active) VALUES (1, :location, :slug, :name, :category, :dimension,"
        " :unit, 'MANUAL', '1.0000', :reorder, NULL, '0.0000', :retail, 2, :unit, :expiry, true)"
    )
    rows = [
        (1, "milk", "Milk", "Dairy", "VOLUME", "l", "2000.0000", None, True),
        (2, "milk", "Milk 2%", None, "VOLUME", "l", "500.0000", None, None),
        (1, "flour", "Flour", None, "WEIGHT", "kg", "0.0000", None, None),
        (2, "flour", "Flour bags", None, "COUNT", "each", "0.0000", None, None),
        (1, "bars", "Protein bar", None, "COUNT", "each", "0.0000", "249.0000", False),
    ]
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants (name, plan, subscription_status, report_day,"
                    " report_hour, timezone, created_at) VALUES ('Corner', 'PRO', 'TRIALING',"
                    " 0, 7, 'UTC', '2026-11-12 12:00:00')"
                )
            )
            for name in ("Main", "Harbor"):
                connection.execute(
                    text(
                        "INSERT INTO locations (tenant_id, name, timezone, currency, is_active)"
                        " VALUES (1, :name, 'UTC', 'USD', true)"
                    ),
                    {"name": name},
                )
            for location, slug, name, category, dimension, unit, reorder, retail, expiry in rows:
                connection.execute(
                    text(stock),
                    {
                        "location": location,
                        "slug": slug,
                        "name": name,
                        "category": category,
                        "dimension": dimension,
                        "unit": unit,
                        "reorder": reorder,
                        "retail": retail,
                        "expiry": expiry,
                    },
                )

        command.upgrade(config, "head")
        with engine.connect() as connection:
            items = connection.execute(
                text(
                    "SELECT slug, name, item_type, dimension, base_unit, display_unit,"
                    " tracks_expiry FROM inventory_items ORDER BY slug"
                )
            ).all()
            links = connection.execute(
                text(
                    "SELECT s.slug, s.location_id, i.slug, l.location_id, l.reorder_point_base"
                    " FROM stock_items s JOIN inventory_items i ON i.id = s.inventory_item_id"
                    " JOIN item_locations l ON l.id = s.item_location_id ORDER BY s.id"
                )
            ).all()
            settings = connection.execute(text("SELECT count(*) FROM item_locations")).scalar()
        assert [tuple(row) for row in items] == [
            ("bars", "Protein bar", "resale", "count", "each", "each", False),
            ("flour", "Flour", "raw", "weight", "g", "kg", None),
            ("flour-count", "Flour bags", "raw", "count", "each", "each", None),
            ("milk", "Milk", "raw", "volume", "ml", "l", True),
        ]
        assert [(a, b, c, d) for a, b, c, d, _ in links] == [
            ("milk", 1, "milk", 1),
            ("milk", 2, "milk", 2),
            ("flour", 1, "flour", 1),
            ("flour", 2, "flour-count", 2),
            ("bars", 1, "bars", 1),
        ]
        assert [str(row[4]).rstrip("0").rstrip(".") for row in links[:2]] == ["2000", "500"]
        assert settings == 5

        command.downgrade(config, BEFORE_DM5)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM stock_items")).scalar() == 5
            assert "inventory_items" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


BEFORE_DM2 = "5c1d0f2a9b31"
NOON = "2026-11-04 12:00:00"
# Rows as they were stored before DM-2: naive UTC wall clocks (NOON). Literals, not binds, so both
# databases receive exactly this text. The test's own constant data.
ROWS_BEFORE_DM2 = [
    "INSERT INTO tenants (name, plan, subscription_status, report_day, report_hour, timezone,"
    " created_at, trial_ends_at) VALUES ('Rosewood', 'PRO', 'TRIALING', 0, 7, 'UTC',"
    " '2026-11-04 12:00:00', '2026-11-04 12:00:00')",
    "INSERT INTO locations (tenant_id, provider, provider_location_id, name, timezone, currency,"
    " is_active) VALUES (1, 'demo', 'l1', 'Main', 'UTC', 'USD', true)",
    "INSERT INTO stock_items (tenant_id, location_id, slug, name, dimension, stock_unit,"
    " track_mode, qty_base, reorder_point_base, cost_per_base_cents, lead_time_days,"
    " receive_unit, is_active) VALUES (1, 1, 'milk', 'Milk', 'COUNT', 'each', 'MANUAL', '-2.0000',"
    " '1.0000', '0.0000', 2, 'each', true)",
    "INSERT INTO inventory_counts (tenant_id, stock_item_id, qty_base, source, counted_at)"
    " VALUES (1, 1, '3.0000', 'manual', '2026-11-04 12:00:00')",
    "INSERT INTO documents (tenant_id, location_id, kind, currency, storage_key, content_type,"
    " sha256, byte_size, status, uploaded_at) VALUES (1, 1, 'invoice', 'USD', '1/a.jpg',"
    " 'image/jpeg', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 12,"
    " 'APPLIED', '2026-11-04 12:00:00')",
    "INSERT INTO document_lines (tenant_id, document_id, line_no, raw_text, parsed_name,"
    " parsed_qty, match_confidence, status) VALUES (1, 1, 1, '2 milk', 'milk', '2.0000',"
    " '0.0000', 'APPLIED')",
]


def test_dm2_keeps_every_instant_and_row_through_upgrade_and_downgrade(fresh_url):
    """Values unchanged when read as UTC (DATA_MIGRATION_ROADMAP, DM-2), and back again."""
    from datetime import datetime

    from app.models import InventoryCount, Tenant

    config = alembic_config(fresh_url)
    command.upgrade(config, BEFORE_DM2)
    engine = create_engine(fresh_url, poolclass=NullPool)
    noon = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
    tables = (
        "tenants",
        "locations",
        "stock_items",
        "inventory_counts",
        "documents",
        "document_lines",
    )

    def counts() -> dict[str, int]:
        with engine.connect() as connection:
            # Table names come from this test's own tuple.
            return {
                table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
                for table in tables
            }

    try:
        with engine.begin() as connection:
            for statement in ROWS_BEFORE_DM2:
                connection.execute(text(statement))

        command.upgrade(config, "head")
        assert counts() == dict.fromkeys(tables, 1)
        with Session(engine) as db:
            tenant = db.scalars(select(Tenant)).one()
            assert tenant.created_at == noon
            assert tenant.trial_ends_at == noon
            assert tenant.trial_ends_at.utcoffset().total_seconds() == 0
            assert db.scalars(select(InventoryCount)).one().counted_at == noon

        command.downgrade(config, BEFORE_DM2)
        assert counts() == dict.fromkeys(tables, 1)
        with engine.connect() as connection:
            stored = connection.execute(text("SELECT trial_ends_at FROM tenants")).scalar()
        assert str(stored).startswith(NOON), stored
    finally:
        engine.dispose()


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
