"""T1.08: confirming an email, resetting a forgotten password, changing a password."""

import hashlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import AuthToken, EmailLog, Location, Tenant, User
from app.web import accounts
from tests.sample_files import jpeg

OWNER = {"email": "owner@corner.test", "password": "a-good-password"}


@pytest.fixture
def mailbox(monkeypatch):
    """Every account email the app sends, with the link it carries."""
    sent: list[dict] = []
    original = accounts.send_email

    def capture(db, tenant_id, to_email, subject, html, kind, text):
        link = re.search(r"https?://\S+token=[\w-]+", text).group(0)
        sent.append({"to": to_email, "kind": kind, "link": link, "text": text})
        return original(db, tenant_id, to_email, subject, html, kind, text)

    monkeypatch.setattr("app.web.accounts.send_email", capture)
    return sent


@pytest.fixture
def client(mailbox):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        test_client.post("/signup", data={"business_name": "Corner", **OWNER})
        yield test_client


def _user() -> User:
    with SessionLocal() as db:
        return db.scalars(select(User)).one()


def _path(link: str) -> str:
    """The path and query of an emailed link, for the test client."""
    return "/" + link.split("://", 1)[1].split("/", 1)[1]


def _token(link: str) -> str:
    return link.rsplit("token=", 1)[1]


def _signed_in(client) -> bool:
    return client.get("/app", follow_redirects=False).status_code == 200


# --- confirming the address (SAAS-A-02) ---------------------------------------------------------


def test_signing_up_sends_a_confirmation_link_and_leaves_the_address_unconfirmed(client, mailbox):
    [mail] = mailbox
    assert mail["kind"] == "verify_email" and mail["to"] == OWNER["email"]
    assert _user().email_verified_at is None
    with SessionLocal() as db:
        [row] = db.scalars(select(AuthToken)).all()
        assert row.token_hash == hashlib.sha256(_token(mail["link"]).encode()).hexdigest()
        assert row.expires_at - row.created_at == timedelta(hours=48)
        assert db.scalars(select(EmailLog)).one().kind == "verify_email"
    assert "Confirm your email" in client.get("/app").text  # the banner


def test_unconfirmed_accounts_cannot_use_ai_or_connect_a_pos(client):
    """AC-SAAS-02 (invitations join the gate when they exist, T1.10)."""
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        location = Location(tenant_id=tenant.id, provider=None, provider_location_id=None, name="M")
        db.add(location)
        db.commit()
        location_id = location.id
    photo = {"photo": ("note.jpg", jpeg("delivery"), "image/jpeg")}
    refused = [
        client.post("/app/receiving", data={"location_id": location_id}, files=photo),
        client.post("/app/scan/expiry", files=photo),
        client.get("/connect/square", follow_redirects=False),
    ]
    assert [response.status_code for response in refused] == [403, 403, 403]
    assert "Confirm your email" in refused[0].text


def test_the_link_confirms_the_address_once(client, mailbox):
    link = mailbox[0]["link"]
    page = client.get(_path(link))
    assert page.status_code == 200 and 'action="/verify-email"' in page.text
    # Opening the page (or a mail scanner fetching it) does not use the link up.
    confirmed = client.post("/verify-email", data={"token": _token(link)}, follow_redirects=False)
    assert confirmed.headers["location"] == "/app?verified=1"
    assert _user().email_verified_at is not None
    assert client.post("/verify-email", data={"token": _token(link)}).status_code == 400
    assert "expired" in client.get(_path(link)).text


def test_a_new_link_replaces_the_old_one(client, mailbox):
    client.post("/app/verify-email/resend")
    first, second = mailbox[0]["link"], mailbox[1]["link"]
    assert first != second
    assert client.post("/verify-email", data={"token": _token(first)}).status_code == 400
    assert client.post("/verify-email", data={"token": _token(second)}).status_code == 200


def test_an_expired_confirmation_link_does_nothing(client, mailbox):
    with SessionLocal() as db:
        db.scalars(select(AuthToken)).one().expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
    token = _token(mailbox[0]["link"])
    assert client.post("/verify-email", data={"token": token}).status_code == 400
    assert _user().email_verified_at is None


# --- a forgotten password (SAAS-A-05, AC-SAAS-04) -----------------------------------------------


def _ask_for_reset(mailbox) -> str:
    with TestClient(app) as stranger:
        stranger.post("/password/forgot", data={"email": OWNER["email"]})
    return [mail for mail in mailbox if mail["kind"] == "reset_password"][-1]["link"]


def test_asking_for_a_reset_says_the_same_whether_or_not_the_account_exists(client, mailbox):
    with TestClient(app) as stranger:
        known = stranger.post("/password/forgot", data={"email": OWNER["email"]})
        unknown = stranger.post("/password/forgot", data={"email": "nobody@corner.test"})
    assert known.status_code == unknown.status_code == 200

    def page(response, email):
        """The answer, less the address typed and the per-request script nonce."""
        return re.sub(r'nonce="[^"]+"', "", response.text.replace(email, "X"))

    assert page(known, OWNER["email"]) == page(unknown, "nobody@corner.test")
    assert [mail["to"] for mail in mailbox if mail["kind"] == "reset_password"] == [OWNER["email"]]


def test_a_reset_link_works_once_within_the_hour_and_ends_every_session(client, mailbox):
    link = _ask_for_reset(mailbox)
    with SessionLocal() as db:
        row = db.scalars(select(AuthToken).where(AuthToken.purpose == "reset_password")).one()
        assert row.expires_at - row.created_at == timedelta(hours=1)

    with TestClient(app) as phone:
        assert "Choose a new password" in phone.get(_path(link)).text
        done = phone.post(
            "/password/reset",
            data={"token": _token(link), "password": "a-new-password-2"},
            follow_redirects=False,
        )
        assert done.headers["location"] == "/login?reset=1"
        again = phone.post(
            "/password/reset", data={"token": _token(link), "password": "a-third-password"}
        )
        assert again.status_code == 400

    assert not _signed_in(client)  # every session ended
    assert _user().email_verified_at is not None  # the email reached them
    with TestClient(app) as laptop:
        old = laptop.post("/login", data=OWNER)
        assert old.status_code == 401
        laptop.post("/login", data={"email": OWNER["email"], "password": "a-new-password-2"})
        assert _signed_in(laptop)


def test_a_short_password_keeps_the_link_usable(client, mailbox):
    link = _ask_for_reset(mailbox)
    with TestClient(app) as phone:
        short = phone.post("/password/reset", data={"token": _token(link), "password": "short"})
        assert short.status_code == 400 and "10 characters" in short.text
        retry = phone.post(
            "/password/reset",
            data={"token": _token(link), "password": "long-enough-now"},
            follow_redirects=False,
        )
        assert retry.status_code == 303


def test_an_expired_reset_link_shows_how_to_get_another(client, mailbox):
    link = _ask_for_reset(mailbox)
    with SessionLocal() as db:
        row = db.scalars(select(AuthToken).where(AuthToken.purpose == "reset_password")).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    with TestClient(app) as phone:
        page = phone.get(_path(link))
        assert "expired" in page.text and 'href="/password/forgot"' in page.text
        refused = phone.post(
            "/password/reset", data={"token": _token(link), "password": "a-new-password-2"}
        )
        assert refused.status_code == 400
    assert _signed_in(client)


def test_a_confirmation_link_cannot_reset_a_password(client, mailbox):
    token = _token(mailbox[0]["link"])
    response = client.post("/password/reset", data={"token": token, "password": "a-new-password-2"})
    assert response.status_code == 400


# --- changing a password while signed in --------------------------------------------------------


def test_changing_the_password_signs_out_every_other_device(client):
    with TestClient(app) as laptop:
        laptop.post("/login", data=OWNER)
        assert _signed_in(laptop)
        changed = client.post(
            "/app/settings/password",
            data={"current_password": OWNER["password"], "new_password": "a-new-password-2"},
            follow_redirects=False,
        )
        assert changed.headers["location"].endswith("password=changed")
        assert _signed_in(client)  # this device stays in, on a fresh session
        assert not _signed_in(laptop)


@pytest.mark.parametrize(
    ("current", "new", "outcome"),
    [("not-my-password", "a-new-password-2", "wrong"), (OWNER["password"], "short", "short")],
)
def test_a_password_change_it_cannot_make_is_refused(client, current, new, outcome):
    response = client.post(
        "/app/settings/password",
        data={"current_password": current, "new_password": new},
        follow_redirects=False,
    )
    assert response.headers["location"].endswith(f"password={outcome}")
    client.post("/logout")
    assert client.post("/login", data=OWNER, follow_redirects=False).status_code == 303
