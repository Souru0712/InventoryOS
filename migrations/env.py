"""Alembic environment. Connection string and metadata both come from the app."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  (registers every mapper on Base.metadata)
from app.config import get_settings
from app.db import Base, Qty

config = context.config
# Programmatic runs pass configure_logger=False so migrations do not reset app logging.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# A caller (tests, tooling) may pin a different database; only fall back to app settings.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """Emit Qty() rather than its dialect impl, which differs on SQLite and Postgres."""
    if type_ == "type" and isinstance(obj, Qty):
        autogen_context.imports.add("from app.db import Qty")
        return "Qty()"
    return False


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_item=render_item,
        compare_type=True,
        compare_server_default=True,
        # SQLite cannot ALTER columns, so alterations are done by table rebuild.
        render_as_batch=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
