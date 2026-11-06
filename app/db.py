"""Engine, session factory, exact-decimal and UTC timestamp column types, cross-process locks."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import (
    Connection,
    DateTime,
    Engine,
    Numeric,
    String,
    TypeDecorator,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings

if TYPE_CHECKING:
    from alembic.config import Config

log = logging.getLogger(__name__)
settings = get_settings()

# Postgres advisory lock keys: one per kind of work that must never run twice at once.
MIGRATION_LOCK = 7_331_001
SCHEDULER_LOCK = 7_331_002


def is_postgres(url: str | None = None) -> bool:
    return (url or settings.database_url).startswith("postgresql")


def _engine_options(url: str) -> dict:
    if not is_postgres(url):
        return {"connect_args": {"check_same_thread": False}}
    return {
        # Managed Postgres drops idle connections; recycle them before it does.
        "pool_recycle": 1800,
        # Server-side prepared statements break behind a transaction-mode pooler and go stale
        # when a schema is rebuilt, so every statement is sent plain.
        "connect_args": {"prepare_threshold": None},
    }


engine = create_engine(
    settings.database_url, pool_pre_ping=True, **_engine_options(settings.database_url)
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(engine, "connect")
def _session_defaults(dbapi_connection, _record) -> None:
    """Postgres sessions speak UTC; SQLite enforces foreign keys, which it skips by default."""
    if engine.dialect.name == "sqlite":
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
        return
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SET TIME ZONE 'UTC'")
    dbapi_connection.commit()


QUANTUM = Decimal("0.0001")


class Qty(TypeDecorator):
    """Exact decimal quantity; stored as text on SQLite to avoid float drift."""

    impl = Numeric(18, 4)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(Numeric(18, 4))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        decimal_value = Decimal(str(value)).quantize(QUANTUM)
        return str(decimal_value) if dialect.name == "sqlite" else decimal_value

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(str(value))


class UTCDateTime(TypeDecorator):
    """An instant (DR-20): written and read as an aware UTC datetime on every database.

    Postgres stores it as timestamptz. SQLite has no zone, so it keeps the UTC wall clock and the
    zone is put back on read. A naive value is refused: its zone is unknown, and guessing hides
    a local-time bug.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"naive datetime {value.isoformat()} has no time zone; pass UTC")
        utc = value.astimezone(UTC)
        return utc.replace(tzinfo=None) if dialect.name == "sqlite" else utc

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def alembic_config(url: str | None = None) -> Config:
    """Alembic config wired to this app, usable from code and from the CLI."""
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", (url or settings.database_url).replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


def run_migrations(url: str | None = None) -> None:
    """Upgrade to head. On Postgres this waits for a lock: web and scheduler start together."""
    from alembic import command

    target = url or settings.database_url
    config = alembic_config(target)
    if not is_postgres(target):
        command.upgrade(config, "head")
        return

    lock_engine = create_engine(target, poolclass=NullPool)
    try:
        with lock_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK})
            try:
                command.upgrade(config, "head")
            finally:
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK})
    finally:
        lock_engine.dispose()


def stamp_migrations(revision: str = "head", url: str | None = None) -> None:
    """Mark an existing schema as already migrated, without running anything."""
    from alembic import command

    command.stamp(alembic_config(url), revision)


def ping() -> None:
    """Raise unless the database answers; for the readiness check, which reads no rows."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def schema_revisions() -> tuple[str | None, str | None]:
    """(revision the database is at, newest revision the code knows about)."""
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    return current, head


def init_db() -> None:
    """Bring the schema to head. Tests build it directly, for speed."""
    from app import models  # noqa: F401  (register mappers)

    if settings.app_env == "test":
        Base.metadata.create_all(engine)
        return
    run_migrations()


class LeaderLock:
    """A Postgres advisory lock held on its own connection for as long as this process wants it.

    Postgres releases the lock the moment the holder's connection dies, so a crashed scheduler
    never locks out the next one. SQLite has no such lock and only ever runs on one machine,
    so there the lock is always granted.
    """

    def __init__(self, key: int, url: str | None = None):
        self.key = key
        self.url = url or settings.database_url
        self._engine: Engine | None = None
        self._connection: Connection | None = None

    def acquire(self) -> bool:
        if not is_postgres(self.url):
            return True
        if self._connection is not None:
            return True
        self._engine = self._engine or create_engine(self.url, poolclass=NullPool)
        connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        got = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": self.key}
        ).scalar()
        if not got:
            connection.close()
            return False
        self._connection = connection
        return True

    def alive(self) -> bool:
        """True while the lock's connection still answers; a dropped connection is a lost lock."""
        if not is_postgres(self.url):
            return True
        if self._connection is None:
            return False
        try:
            self._connection.execute(text("SELECT 1"))
            return True
        except Exception:
            log.exception("connection holding advisory lock %s failed", self.key)
            return False

    def release(self) -> None:
        if self._connection is not None:
            try:
                self._connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.key})
            except Exception:
                log.warning(
                    "advisory lock %s was not released cleanly; closing its connection", self.key
                )
            finally:
                self._connection.close()
                self._connection = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
