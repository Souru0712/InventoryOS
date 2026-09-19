"""Test environment must be configured before any app module reads settings."""
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_TMP = Path(tempfile.mkdtemp(prefix="stockpulse-tests-"))
# Point TEST_DATABASE_URL at an empty Postgres database to run the whole suite there instead.
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}"
if _DATABASE_URL.startswith("postgres"):
    # A test that deadlocks against itself should fail in seconds, not hang the run.
    os.environ.setdefault("PGOPTIONS", "-c lock_timeout=10000 -c statement_timeout=120000")

os.environ.update(
    APP_ENV="test",
    DATABASE_URL=_DATABASE_URL,
    ENCRYPTION_KEY=Fernet.generate_key().decode(),
    SECRET_KEY="test-secret-key",
    STORAGE_BACKEND="local",
    UPLOAD_DIR=str(_TMP / "uploads"),
    SENTRY_DSN="",
    SMTP_HOST="",
    ANTHROPIC_API_KEY="",
    SQUARE_APP_ID="test-app",
    SQUARE_APP_SECRET="test-secret",
)

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Location, Tenant, User  # noqa: E402
from app.security import hash_password  # noqa: E402


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def tenant(db):
    tenant = Tenant(name="Rosewood Coffee")
    db.add(tenant)
    db.flush()
    db.add(
        User(
            tenant_id=tenant.id,
            email="owner@rosewood.test",
            password_hash=hash_password("a-good-password"),
        )
    )
    db.add(
        Location(
            tenant_id=tenant.id,
            provider="demo",
            provider_location_id="loc-1",
            name="Main",
            timezone="UTC",
        )
    )
    db.commit()
    return tenant


@pytest.fixture
def location(db, tenant):
    return db.query(Location).filter(Location.tenant_id == tenant.id).one()


@pytest.fixture(autouse=True)
def private_modules_off():
    """Snack boxes stay off unless a test turns them on, exactly as they are off for a customer."""
    from app.config import get_settings

    get_settings().snack_box_tenant_ids = ""
    yield
