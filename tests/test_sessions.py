"""T1.07: revocable server-side sessions, CSRF tokens on every state change, POST logout."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Location, Membership, Tenant, User, UserSession
from app.security import hash_password, revoke_membership
from tests.browser import session_of
from tests.sample_files import jpeg

NO_TOKEN = {"X-CSRF-Token": ""}
OWNER = {"email": "owner@corner.test", "password": "a-good-password"}


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        test_client.post(
            "/signup", data={"business_name": "Corner", **OWNER}, follow_redirects=False
        )
        yield test_client


def _sessions() -> list[UserSession]:
    with SessionLocal() as db:
        return list(db.scalars(select(UserSession).order_by(UserSession.id)))


def _signed_in(client: TestClient) -> bool:
    return client.get("/app", follow_redirects=False).status_code == 200


# --- the session lives on the server ------------------------------------------------------------


def test_the_cookie_carries_a_token_and_nothing_about_the_person(client):
    cookie = session_of(client)
    assert set(cookie) <= {"sid", "csrf"}
    [row] = _sessions()
    assert row.token_hash == hashlib.sha256(cookie["sid"].encode()).hexdigest()
    assert row.token_hash != cookie["sid"]  # only the hash is stored
    assert row.revoked_at is None and row.expires_at > datetime.now(UTC)


def test_signing_in_starts_a_new_session_and_ends_the_one_before(client):
    first = session_of(client)["sid"]
    client.post("/login", data=OWNER)
    second = session_of(client)["sid"]
    assert first != second
    old, new = _sessions()
    assert old.revoked_at is not None and new.revoked_at is None


def test_logging_out_ends_the_session_for_good(client):
    stolen = client.cookies.get("session")
    assert client.post("/logout", follow_redirects=False).status_code == 303
    assert _sessions()[0].revoked_at is not None

    replayed = TestClient(app)
    replayed.cookies.set("session", stolen)
    response = replayed.get("/app", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"


def test_logging_out_takes_a_post(client):
    assert client.get("/logout").status_code == 405
    assert _signed_in(client)


@pytest.mark.parametrize(
    ("column", "age"), [("last_seen_at", timedelta(hours=13)), ("expires_at", timedelta(0))]
)
def test_an_idle_or_expired_session_ends(client, column, age):
    """Twelve hours idle, or fourteen days in all, ends a session (SAAS-A-06)."""
    with SessionLocal() as db:
        row = db.scalars(select(UserSession)).one()
        setattr(row, column, datetime.now(UTC) - age)
        db.commit()
    assert not _signed_in(client)


def test_a_session_in_use_stays_alive(client):
    with SessionLocal() as db:
        row = db.scalars(select(UserSession)).one()
        row.last_seen_at = datetime.now(UTC) - timedelta(hours=11)
        db.commit()
    assert _signed_in(client)
    with SessionLocal() as db:
        seen = db.scalars(select(UserSession)).one().last_seen_at
    assert datetime.now(UTC) - seen < timedelta(minutes=1)  # the request moved it on


def test_logging_out_everywhere_ends_every_device(client):
    laptop = TestClient(app)
    laptop.post("/login", data=OWNER)
    assert _signed_in(client) and _signed_in(laptop)

    client.post("/app/sessions/end-all")
    assert not _signed_in(client)
    assert not _signed_in(laptop)
    assert all(row.revoked_at is not None for row in _sessions())


def test_the_business_chosen_is_kept_on_the_server(client):
    with SessionLocal() as db:
        owner = db.scalars(select(User)).one()
        other = Tenant(name="Second Shop")
        db.add(other)
        db.flush()
        joined = Membership(tenant_id=other.id, user_id=owner.id, role="manager")
        db.add(joined)
        db.commit()
        joined_id = joined.id
    client.post("/app/organizations/switch", data={"membership_id": joined_id})
    assert _sessions()[-1].membership_id == joined_id
    assert "mid" not in session_of(client)


# --- revoking a member (STF-FR-06, AC-STF-04) ----------------------------------------------------


def test_revoking_a_member_blocks_their_next_request_and_ends_their_session(client):
    staff = TestClient(app)
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        user = User(
            tenant_id=tenant.id,
            email="sam@corner.test",
            password_hash=hash_password("staff-password-1"),
        )
        db.add(user)
        db.flush()
        membership = Membership(tenant_id=tenant.id, user_id=user.id, role="employee")
        db.add(membership)
        db.commit()
        membership_id = membership.id
    staff.post("/login", data={"email": "sam@corner.test", "password": "staff-password-1"})
    assert _signed_in(staff)
    with SessionLocal() as db:
        staff_session = db.scalars(select(UserSession).where(UserSession.user_id == user.id)).one()
        staff_session.membership_id = membership_id  # acting for this business
        db.commit()

    with SessionLocal() as db:
        revoke_membership(db, db.get(Membership, membership_id))
        db.commit()

    assert not _signed_in(staff)  # the very next request
    with SessionLocal() as db:
        assert db.get(UserSession, staff_session.id).revoked_at is not None
    assert _signed_in(client)  # nobody else is affected


# --- CSRF (SEC-05, probe R8) --------------------------------------------------------------------


def test_every_form_that_posts_carries_the_token():
    templates = Path("app/web/templates").rglob("*.html")
    missing = []
    for path in templates:
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<form\b[^>]*\bmethod=\"post\"[^>]*>", text, re.IGNORECASE):
            if not text[match.end() :].lstrip().startswith("{{ csrf_field(request) }}"):
                missing.append(f"{path}:{text[: match.start()].count(chr(10)) + 1}")
    assert missing == []


def test_the_login_page_carries_a_form_token():
    """Probe R8's CSRF half, inverted: forms had no token."""
    with TestClient(app) as anonymous:
        page = anonymous.get("/login").text
    assert re.search(r'name="csrf_token" value="[\w-]{40,}"', page)


@pytest.mark.parametrize("token", ["", "forged-token-of-the-right-length-0123456789"])
def test_a_post_without_this_sessions_token_is_refused(client, token):
    response = client.post(
        "/app/settings/business",
        data={"business_name": "Hijacked"},
        headers={"X-CSRF-Token": token},
        follow_redirects=False,
    )
    assert response.status_code == 403
    with SessionLocal() as db:
        assert db.scalars(select(Tenant)).one().name == "Corner"


def test_another_sessions_token_is_refused(client):
    with TestClient(app) as other:
        other.get("/login")
        theirs = session_of(other)["csrf"]
    response = client.post(
        "/app/settings/business",
        data={"business_name": "Hijacked"},
        headers={"X-CSRF-Token": theirs},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_the_token_works_as_a_form_field_even_in_an_upload(client):
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        location = Location(
            tenant_id=tenant.id, provider=None, provider_location_id=None, name="Main"
        )
        db.add(location)
        db.commit()
        location_id = location.id
    client.get("/app/receiving")  # the page with the upload form issues the token
    response = client.post(
        "/app/receiving",
        data={"location_id": location_id, "csrf_token": session_of(client)["csrf"]},
        files={"photo": ("note.jpg", jpeg("delivery"), "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "error" not in response.headers["location"]


def test_signing_in_issues_a_new_form_token(client):
    client.get("/login")
    before = session_of(client)["csrf"]
    client.post("/login", data=OWNER)
    client.get("/app")
    assert session_of(client)["csrf"] != before


def test_provider_webhooks_need_no_token_they_are_signed(client):
    """Square and Stripe post without cookies; their signature is the check."""
    for path in ("/webhooks/square", "/billing/webhook"):
        response = client.post(path, content=b"{}", headers=NO_TOKEN)
        assert response.status_code != 403, path


def test_a_refused_post_says_what_to_do(client):
    response = client.post("/app/sessions/end-all", headers=NO_TOKEN)
    assert response.status_code == 403
    assert "reload the page" in response.text
    assert _signed_in(client)
