"""T1.09: sign-in and emailed-link forms are throttled by account and by address (SAAS-A-03)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Throttle
from app.throttle import LOGIN_ACCOUNT, LOGIN_IP, hit, prune
from app.web import accounts

OWNER = {"email": "owner@corner.test", "password": "a-good-password"}
WRONG = {"email": OWNER["email"], "password": "not-the-password"}


@pytest.fixture
def mailbox(monkeypatch):
    sent: list[dict] = []
    original = accounts.send_email

    def capture(db, tenant_id, to_email, subject, html, kind, text):
        sent.append({"to": to_email, "kind": kind, "text": text})
        return original(db, tenant_id, to_email, subject, html, kind, text)

    monkeypatch.setattr("app.web.accounts.send_email", capture)
    return sent


@pytest.fixture
def owner(mailbox):
    """An account exists; nobody is signed in."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as signup:
        signup.post("/signup", data={"business_name": "Corner", **OWNER})
    mailbox.clear()
    return OWNER


def visitor(ip: str = "198.51.100.7") -> TestClient:
    return TestClient(app, client=(ip, 50000))


def _rows() -> list[Throttle]:
    with SessionLocal() as db:
        return list(db.scalars(select(Throttle)))


def _age(rule_name: str, **changes) -> None:
    """Move a counter's clock, as if time had passed."""
    with SessionLocal() as db:
        for row in db.scalars(select(Throttle)):
            if row.key.startswith(rule_name + ":"):
                for column, value in changes.items():
                    setattr(row, column, value)
        db.commit()


# --- sign-in (AC-SAAS-03) -----------------------------------------------------------------------


def test_five_wrong_passwords_lock_the_account_even_against_the_right_one(owner):
    with visitor() as browser:
        codes = [browser.post("/login", data=WRONG).status_code for _ in range(5)]
        locked = browser.post("/login", data=owner, follow_redirects=False)
    assert codes == [401] * 5
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0
    assert "Too many attempts" in locked.text


def test_counters_hold_no_email_or_address(owner):
    with visitor("203.0.113.9") as browser:
        browser.post("/login", data=WRONG)
    keys = " ".join(row.key for row in _rows())
    assert keys and OWNER["email"] not in keys and "203.0.113.9" not in keys


def test_the_lock_ends_and_a_good_sign_in_starts_the_count_again(owner):
    with visitor() as browser:
        for _ in range(5):
            browser.post("/login", data=WRONG)
        past = datetime.now(UTC) - timedelta(seconds=1)
        _age(LOGIN_ACCOUNT.name, locked_until=past, window_started_at=past - timedelta(hours=1))
        _age(LOGIN_IP.name, locked_until=past)
        assert browser.post("/login", data=owner, follow_redirects=False).status_code == 303
    assert not [row for row in _rows() if row.key.startswith(LOGIN_ACCOUNT.name)]


def test_each_guess_past_the_limit_locks_for_twice_as_long(owner):
    with SessionLocal() as db:
        for _ in range(5):
            hit(db, LOGIN_ACCOUNT, OWNER["email"])
        first = db.scalars(select(Throttle)).one()
        first_lock = first.locked_until - datetime.now(UTC)
        hit(db, LOGIN_ACCOUNT, OWNER["email"])
        second_lock = first.locked_until - datetime.now(UTC)
        db.commit()
    assert timedelta(seconds=50) < first_lock <= timedelta(minutes=1)
    assert timedelta(seconds=110) < second_lock <= timedelta(minutes=2)


def test_one_address_guessing_at_many_accounts_is_stopped(owner):
    with visitor("192.0.2.44") as guesser:
        for n in range(20):
            guesser.post("/login", data={"email": f"user{n}@corner.test", "password": "x" * 12})
        assert guesser.post("/login", data=owner, follow_redirects=False).status_code == 429
    with visitor("192.0.2.45") as someone_else:
        assert someone_else.post("/login", data=owner, follow_redirects=False).status_code == 303


def test_guesses_from_many_addresses_add_up_for_one_account(owner):
    """The counters are rows in the database, so every server and address counts together."""
    for n in range(5):
        with visitor(f"192.0.2.{n + 1}") as browser:
            browser.post("/login", data=WRONG)
    with visitor("192.0.2.99") as browser:
        assert browser.post("/login", data=owner, follow_redirects=False).status_code == 429


def test_a_locked_unknown_account_reads_the_same_as_a_locked_real_one(owner):
    unknown = {"email": "nobody@corner.test", "password": "not-the-password"}
    replies = []
    for credentials, ip in ((WRONG, "203.0.113.1"), (unknown, "203.0.113.2")):
        with visitor(ip) as browser:
            for _ in range(5):
                browser.post("/login", data=credentials)
            reply = browser.post("/login", data=credentials)
            replies.append((reply.status_code, "Too many attempts" in reply.text))
    assert replies[0] == replies[1] == (429, True)


def test_a_good_sign_in_does_not_wipe_the_addresss_count(owner):
    with visitor("198.51.100.20") as browser:
        for n in range(19):
            browser.post("/login", data={"email": f"x{n}@corner.test", "password": "x" * 12})
        assert browser.post("/login", data=owner, follow_redirects=False).status_code == 303
        browser.post("/logout")
        browser.post("/login", data={"email": "x99@corner.test", "password": "x" * 12})
        assert browser.post("/login", data=owner, follow_redirects=False).status_code == 429


# --- emailed links ------------------------------------------------------------------------------


def test_reset_emails_stop_after_three_an_hour_and_the_page_never_says(owner, mailbox):
    with visitor() as stranger:
        pages = [
            stranger.post("/password/forgot", data={"email": OWNER["email"]}) for _ in range(5)
        ]
    assert [page.status_code for page in pages] == [200] * 5
    assert all("Check your email" in page.text for page in pages)
    assert len([mail for mail in mailbox if mail["kind"] == "reset_password"]) == 3


def test_one_address_cannot_send_reset_emails_to_everyone(owner, mailbox):
    with visitor("198.51.100.30") as flooder:
        for n in range(10):
            flooder.post("/password/forgot", data={"email": f"x{n}@corner.test"})
        flooder.post("/password/forgot", data={"email": OWNER["email"]})
    assert mailbox == []  # the eleventh request from that address sent nothing


def test_confirmation_links_cannot_be_resent_endlessly(owner, mailbox):
    with visitor() as browser:
        browser.post("/login", data=owner)
        answers = [
            browser.post("/app/verify-email/resend", follow_redirects=False).headers["location"]
            for _ in range(4)
        ]
    assert answers[:3] == ["/app/settings?verification=sent"] * 3
    assert answers[3] == "/app/settings?verification=wait"
    assert len(mailbox) == 3


def test_guessing_at_links_is_stopped(owner, mailbox):
    with visitor("198.51.100.40") as browser:
        tries = [
            browser.post("/verify-email", data={"token": f"guess-{n}"}).status_code
            for n in range(10)
        ]
        stopped = browser.post("/password/reset", data={"token": "guess", "password": "x" * 12})
    assert tries == [400] * 10
    assert stopped.status_code == 429


def test_a_real_link_still_works_from_another_address(owner, mailbox):
    with visitor("198.51.100.50") as browser:
        for n in range(10):
            browser.post("/verify-email", data={"token": f"guess-{n}"})
    with visitor() as signed_in:
        signed_in.post("/login", data=owner)
        signed_in.post("/app/verify-email/resend")
    token = mailbox[-1]["text"].split("token=", 1)[1].split()[0]
    with visitor("198.51.100.51") as owner_phone:
        done = owner_phone.post("/verify-email", data={"token": token}, follow_redirects=False)
    assert done.status_code == 303


# --- housekeeping -------------------------------------------------------------------------------


def test_old_counters_are_pruned_and_live_ones_kept(owner):
    with visitor() as browser:
        browser.post("/login", data=WRONG)
    with SessionLocal() as db:
        hit(db, LOGIN_ACCOUNT, "stale@corner.test")
        stale = db.scalars(select(Throttle)).all()[-1]
        stale.updated_at = datetime.now(UTC) - timedelta(days=2)
        db.commit()
        assert prune(db) == 1
        db.commit()
    assert len(_rows()) == 2  # the account and address counters from the live attempt
