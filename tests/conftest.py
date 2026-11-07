"""Test environment must be configured before any app module reads settings."""

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

# Importing the Settings class reads nothing; settings are only read on first get_settings().
from app.config import Settings  # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="stockpulse-tests-"))
# Point TEST_DATABASE_URL at an empty Postgres database to run the whole suite there instead.
_DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{(_TMP / 'test.db').as_posix()}"
if _DATABASE_URL.startswith("postgres"):
    # A test that deadlocks against itself should fail in seconds, not hang the run.
    os.environ.setdefault("PGOPTIONS", "-c lock_timeout=10000 -c statement_timeout=120000")

# Hermetic: no setting may come from the shell that launched the tests. Every field is either pinned
# below or takes its default from code. Settings ignore .env under APP_ENV=test (app/config.py).
for _field in Settings.model_fields:
    os.environ.pop(_field.upper(), None)

os.environ.update(
    APP_ENV="test",
    APP_BASE_URL="http://localhost:8000",
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

from datetime import UTC, datetime  # noqa: E402

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import Location, Membership, Tenant, User  # noqa: E402
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
    owner = User(
        tenant_id=tenant.id,
        email="owner@rosewood.test",
        password_hash=hash_password("a-good-password"),
        email_verified_at=datetime.now(UTC),
    )
    db.add(owner)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=owner.id, role="owner"))
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


# Tests post like a browser: with the session's form token (tests/browser.py, T1.07).
import tests.browser  # noqa: E402, F401
