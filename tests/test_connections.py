"""T3.02: the POS connection lifecycle (POS-FR-01, SQ-01..04, DR-22; AC-POS-01, -09, -11).

Square is replaced at its OAuth module and client; nothing reaches the network.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.access import OrgContext
from app.config import get_settings
from app.connections import (
    MerchantTaken,
    NotConnected,
    connect,
    credentials,
    disconnect,
    email_owners,
)
from app.crypto import decrypt
from app.db import Base, SessionLocal, engine
from app.jobs import drain
from app.main import app
from app.models import (
    AuditEvent,
    ExceptionRecord,
    Membership,
    ProviderConnection,
    SyncRun,
    Tenant,
    User,
)
from app.orchestration import sync
from app.providers.base import AdapterError, Credentials
from app.providers.square import adapter as square_adapter
from app.providers.square import oauth
from app.providers.square.client import SquareError
from app.providers.square.webhooks import signature_for
from app.security import hash_password
from tests.accounts import confirm_email

MERCHANT = "M-1"
OWNER = {"email": "owner@corner.test", "password": "a-good-password"}


class FakeSquare:
    """Square's token endpoints: what was exchanged, refreshed and revoked."""

    def __init__(self):
        self.refused: int | None = None  # the status the token endpoints answer with
        self.refreshed = 0
        self.revoked: list[str] = []

    def exchange_code(self, code):
        return {
            "access_token": f"access-{code}",
            "refresh_token": f"refresh-{code}",
            "merchant_id": MERCHANT,
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        }

    def refresh(self, refresh_token):
        if self.refused:
            raise oauth.OAuthError("refused", self.refused)
        self.refreshed += 1
        return {
            "access_token": "access-new",
            "refresh_token": None,
            "merchant_id": MERCHANT,
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        }

    def revoke(self, access_token):
        if self.refused:
            raise oauth.OAuthError("refused", self.refused)
        self.revoked.append(access_token)


@pytest.fixture
def square(monkeypatch):
    fake = FakeSquare()
    for name in ("exchange_code", "refresh", "revoke"):
        monkeypatch.setattr(oauth, name, getattr(fake, name))
    monkeypatch.setattr(get_settings(), "square_app_id", "app-id", raising=False)
    monkeypatch.setattr("app.web.dashboard.run_sync", lambda db, org: None)
    return fake


def held(expires_in=timedelta(days=30), refresh: str | None = "r") -> Credentials:
    return Credentials("a", refresh, datetime.now(UTC) + expires_in, MERCHANT)


def business(db, name: str) -> OrgContext:
    other = Tenant(name=name)
    db.add(other)
    db.flush()
    return OrgContext.system(other.id)


def connection_of(db, tenant_id: int) -> ProviderConnection:
    return db.scalars(
        select(ProviderConnection).where(ProviderConnection.tenant_id == tenant_id)
    ).one()


def audit(db, action: str) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def sync_failures(db) -> list[ExceptionRecord]:
    return list(db.scalars(select(ExceptionRecord).where(ExceptionRecord.type == "sync_failure")))


# --- one merchant, one business (DR-22) ---------------------------------------------------------


def test_pa4_retired_a_merchant_is_actively_connected_to_one_business_at_most(db, tenant):
    """The database refuses a second active connection to the same merchant (probe PA4)."""
    other = business(db, "Harbor Bakery")
    for tenant_id in (tenant.id, other.tenant_id):
        db.add(
            ProviderConnection(
                tenant_id=tenant_id, provider="square", merchant_id=MERCHANT, access_token_enc="x"
            )
        )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # Once one has let go, the other may hold it.
    db.add(
        ProviderConnection(
            tenant_id=tenant.id, provider="square", merchant_id=MERCHANT, status="disconnected"
        )
    )
    db.add(
        ProviderConnection(
            tenant_id=business(db, "Harbor Bakery").tenant_id,
            provider="square",
            merchant_id=MERCHANT,
            access_token_enc="x",
        )
    )
    db.commit()


def test_an_active_connection_holds_a_token(db, tenant):
    db.add(ProviderConnection(tenant_id=tenant.id, provider="square", merchant_id=MERCHANT))
    with pytest.raises(IntegrityError):
        db.commit()


def test_ac_pos_11_a_second_business_cannot_connect_the_same_merchant(db, tenant, square):
    first = OrgContext.system(tenant.id)
    connect(db, first, "square", held())
    db.commit()
    second = business(db, "Harbor Bakery")
    with pytest.raises(MerchantTaken):
        connect(db, second, "square", held())
    assert square.revoked == [], "revoking would end the first business's access too"
    assert connection_of(db, tenant.id).status == "active"
    assert not db.scalars(
        select(ProviderConnection).where(ProviderConnection.tenant_id == second.tenant_id)
    ).all()

    disconnect(db, first)
    connect(db, second, "square", held())
    db.commit()
    assert connection_of(db, second.tenant_id).status == "active"


# --- connect and disconnect through the screens (AC-POS-01, SQ-04) ------------------------------


def browser() -> TestClient:
    client = TestClient(app, client=("203.0.113.5", 50000))
    client.__enter__()
    return client


@pytest.fixture
def owner():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client = browser()
    client.post("/signup", data={"business_name": "Corner Cafe", **OWNER})
    confirm_email(OWNER["email"])
    yield client
    client.__exit__(None, None, None)


def connect_through_square(client: TestClient):
    start = client.get("/connect/square", follow_redirects=False)
    query = parse_qs(urlparse(start.headers["location"]).query)
    callback = client.get(
        "/connect/square/callback",
        params={"code": "c", "state": query["state"][0]},
        follow_redirects=False,
    )
    return query, callback


def test_ac_pos_01_connecting_asks_for_product_scopes_and_stores_tokens_encrypted(owner, square):
    query, callback = connect_through_square(owner)
    assert query["scope"] == ["MERCHANT_PROFILE_READ ITEMS_READ ORDERS_READ"]
    assert callback.headers["location"] == "/app?connected=1"
    with SessionLocal() as db:
        connection = db.scalars(select(ProviderConnection)).one()
        assert connection.status == "active" and connection.merchant_id == MERCHANT
        assert "access-c" not in connection.access_token_enc
        assert decrypt(connection.access_token_enc) == "access-c"
        assert decrypt(connection.refresh_token_enc) == "refresh-c"
    page = owner.get("/app/connection").text
    assert "Connected." in page and MERCHANT in page and "Disconnect Square" in page

    # Another business signing in with the same Square account is turned away, told why.
    second = browser()
    second.post(
        "/signup",
        data={
            "business_name": "Harbor Bakery",
            "email": "o@harbor.test",
            "password": OWNER["password"],
        },
    )
    confirm_email("o@harbor.test")
    _query, refused = connect_through_square(second)
    assert refused.headers["location"] == "/app/connection?error=merchant_taken"
    assert "already connected to another business" in second.get(refused.headers["location"]).text
    second.__exit__(None, None, None)


def test_disconnecting_revokes_at_square_stops_ingestion_and_keeps_history(owner, square):
    connect_through_square(owner)
    page = owner.post("/app/connection/disconnect").text
    assert "Square is disconnected. Your history is kept." in page
    assert "Reconnect Square" in page
    assert square.revoked == ["access-c"]
    with SessionLocal() as db:
        connection = db.scalars(select(ProviderConnection)).one()
        assert connection.status == "disconnected"
        assert connection.access_token_enc is None and connection.refresh_token_enc is None
        (event,) = audit(db, "connection.disconnected")
        assert event.after == {"status": "disconnected", "revoked_at_provider": True}
        with pytest.raises(NotConnected):
            sync.run_sync(db, OrgContext.system(connection.tenant_id))

    connect_through_square(owner)
    with SessionLocal() as db:
        assert db.scalars(select(ProviderConnection)).one().status == "active"
        assert [e.summary for e in audit(db, "connection.connected")] == [
            "Connected Square",
            "Reconnected Square",
        ]


def test_a_disconnect_square_cannot_confirm_still_lets_go_and_says_so(db, tenant, square):
    org = OrgContext.system(tenant.id)
    connect(db, org, "square", held())
    square.refused = 503
    assert disconnect(db, org) is False
    db.commit()
    connection = connection_of(db, tenant.id)
    assert connection.status == "disconnected" and connection.access_token_enc is None
    assert audit(db, "connection.disconnected")[0].after["revoked_at_provider"] is False
    with pytest.raises(NotConnected):
        disconnect(db, org)


# --- refresh and revocation (SQ-01, AC-POS-09) --------------------------------------------------


def test_a_token_about_to_lapse_is_refreshed_and_kept_encrypted(db, tenant, square):
    org = OrgContext.system(tenant.id)
    connection = connect(db, org, "square", held(expires_in=timedelta(days=2)))
    fresh = credentials(db, org, connection)
    assert (fresh.access_token, fresh.refresh_token) == ("access-new", "r")
    assert decrypt(connection.access_token_enc) == "access-new"
    assert decrypt(connection.refresh_token_enc) == "r"  # Square sent none: the old one stands
    credentials(db, org, connection)
    assert square.refreshed == 1, "a fresh token is not refreshed again"


def test_a_token_with_nothing_to_refresh_it_is_used_until_it_expires(db, tenant, square):
    org = OrgContext.system(tenant.id)
    connection = connect(db, org, "square", held(expires_in=timedelta(days=2), refresh=None))
    assert credentials(db, org, connection).access_token == "a"
    connection.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    with pytest.raises(NotConnected):
        credentials(db, org, connection)
    assert connection.status == "revoked"


def test_ac_pos_09_a_revoked_authorization_is_marked_raised_and_emailed_to_the_owner(
    db, tenant, square, monkeypatch
):
    manager = User(
        tenant_id=tenant.id, email="m@rosewood.test", password_hash=hash_password("pw-long-1")
    )
    db.add(manager)
    db.flush()
    db.add(Membership(tenant_id=tenant.id, user_id=manager.id, role="manager"))
    org = OrgContext.system(tenant.id)
    connection = connect(db, org, "square", held(expires_in=timedelta(days=1)))
    db.commit()

    square.refused = 401
    with pytest.raises(NotConnected):
        credentials(db, org, connection)
    db.commit()
    assert connection.status == "revoked"
    (exception,) = sync_failures(db)
    assert (exception.severity, exception.status) == ("critical", "open")
    assert exception.subject_type == "provider_connection"
    assert len(audit(db, "connection.revoked")) == 1

    sent: list[tuple[str, str]] = []

    def capture(db, tenant_id, to_email, subject, html, kind, text):
        assert "/app/connection" in text
        sent.append((to_email, kind))
        return True

    monkeypatch.setattr("app.connections.send_email", capture)
    drain()
    assert sent == [("owner@rosewood.test", "connection_revoked")], "the Owner, not the manager"
    with SessionLocal() as other:
        assert email_owners(other, org, connection.id) == 0, "once per revocation"

    db.expire_all()
    connect(db, org, "square", held())
    db.commit()
    assert connection_of(db, tenant.id).status == "active"
    assert sync_failures(db)[0].status == "resolved", "reconnecting fixes it"


def test_square_refusing_the_sync_marks_the_connection_revoked(db, tenant, square, monkeypatch):
    org = OrgContext.system(tenant.id)
    connect(db, org, "square", held())
    db.commit()
    status = {"code": 503}

    class RefusingClient:
        def __init__(self, token):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def locations(self):
            raise SquareError("GET", "/v2/locations", status["code"], "{}")

    monkeypatch.setattr(square_adapter, "SquareClient", RefusingClient)
    with pytest.raises(AdapterError):
        sync.run_sync(db, org)
    assert connection_of(db, tenant.id).status == "active", "a passing failure is not revocation"
    failed = db.scalars(select(SyncRun)).one()
    assert (failed.kind, failed.status.value) == ("catalog", "failed"), "kept for the history"

    status["code"] = 401
    assert sync.run_sync(db, org) == {"revoked": 1}, "marked, not retried"
    assert connection_of(db, tenant.id).status == "revoked"
    assert sync_failures(db)[0].severity == "critical"


def test_square_revoking_the_app_is_heard_from_its_signed_webhook(db, tenant, square, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "square_webhook_signature_key", "key", raising=False)
    monkeypatch.setattr(settings, "app_base_url", "https://app.test", raising=False)
    connect(db, OrgContext.system(tenant.id), "square", held())
    db.commit()

    def post(client, merchant):
        body = json.dumps(
            {
                "merchant_id": merchant,
                "type": "oauth.authorization.revoked",
                "event_id": f"evt-{merchant}",
                "data": {"type": "revocation", "object": {"revocation": {}}},
            }
        ).encode()
        signature = signature_for("https://app.test/webhooks/square", body, "key")
        return client.post(
            "/webhooks/square", content=body, headers={"x-square-hmacsha256-signature": signature}
        ).json()

    with TestClient(app) as client:
        assert post(client, "SOMEONE-ELSE") == {"revoked": False}
        assert post(client, MERCHANT) == {"revoked": True}
    db.expire_all()
    assert connection_of(db, tenant.id).status == "revoked"
    assert sync_failures(db)[0].severity == "critical"


# --- the rules -----------------------------------------------------------------------------------


def test_write_back_alone_asks_square_for_inventory(monkeypatch):
    monkeypatch.setattr(get_settings(), "square_write_back", True, raising=False)
    assert oauth.scopes()[-2:] == ("INVENTORY_READ", "INVENTORY_WRITE")


def test_only_the_connection_service_writes_connections():
    """Connections are created, re-tokened and closed in one place (ADR-0076)."""
    writes = re.compile(
        r"ProviderConnection\(|\.(access|refresh)_token_enc\s*=[^=]|update_scoped\(\w+, ProviderConnection"
    )
    offenders = [
        path.as_posix()
        for path in Path("app").rglob("*.py")
        if path.as_posix() not in {"app/connections.py", "app/models.py"}
        and writes.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
