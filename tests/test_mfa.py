"""T1.11: two-step sign-in with an authenticator app, and recovery codes (SAAS-A-07)."""

import argparse
import re
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.cli import cmd_reset_mfa
from app.config import get_settings
from app.crypto import decrypt
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import RecoveryCode, User, UserSession
from app.transform.mfa import matching_step, new_recovery_codes, recovery_hash
from tests.accounts import confirm_email

OWNER = {"email": "owner@corner.test", "password": "a-good-password"}
SETUP_KEY = re.compile(r'<code style="[^"]*">([A-Z2-7 ]+)</code>')
CODES = re.compile(r"<li>([a-z2-9]{5}-[a-z2-9]{5})</li>")


def browser() -> TestClient:
    client = TestClient(app)
    client.__enter__()
    return client


@pytest.fixture
def owner():
    """Corner Cafe's owner, signed in, email confirmed, two-step sign-in not yet on."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    client = browser()
    client.post("/signup", data={"business_name": "Corner Cafe", **OWNER})
    confirm_email(OWNER["email"])
    yield client
    client.__exit__(None, None, None)


def code(secret: str, ahead: int = 0) -> str:
    """The authenticator's code for now, or for a step ahead (still inside the drift window)."""
    totp = pyotp.TOTP(secret)
    return totp.generate_otp(totp.timecode(datetime.now(UTC)) + ahead)


def turn_on(client: TestClient) -> tuple[str, list[str]]:
    """Enrol the signed-in person; their secret and recovery codes."""
    page = client.get("/app/settings/mfa").text
    secret = SETUP_KEY.search(page).group(1).replace(" ", "")
    done = client.post("/app/settings/mfa/enable", data={"code": code(secret)})
    assert done.status_code == 200, done.text[:300]
    return secret, CODES.findall(done.text)


def user_row() -> User:
    with SessionLocal() as db:
        return db.scalars(select(User).where(User.email == OWNER["email"])).one()


def password_step(client: TestClient):
    return client.post("/login", data=OWNER, follow_redirects=False)


# --- enrolling ----------------------------------------------------------------------------------


def test_turning_it_on_takes_a_code_and_shows_ten_recovery_codes_once(owner):
    page = owner.get("/app/settings/mfa")
    assert "<svg" in page.text and SETUP_KEY.search(page.text)
    secret = SETUP_KEY.search(page.text).group(1).replace(" ", "")
    reload = owner.get("/app/settings/mfa").text
    assert SETUP_KEY.search(reload).group(1).replace(" ", "") == secret  # a scanned code stays good

    wrong = owner.post("/app/settings/mfa/enable", data={"code": "000000"})
    assert wrong.status_code == 400 and user_row().mfa_enabled_at is None

    secret, codes = turn_on(owner)
    assert len(codes) == 10 and len(set(codes)) == 10
    row = user_row()
    assert row.mfa_enabled_at is not None
    assert secret not in row.mfa_secret_enc  # encrypted with the application key
    with SessionLocal() as db:
        stored = [r.code_hash for r in db.scalars(select(RecoveryCode))]
    assert sorted(stored) == sorted(recovery_hash(c) for c in codes)
    assert not set(codes) & set(stored)
    assert not CODES.findall(owner.get("/app/settings/mfa").text)  # shown once only


def test_turning_it_on_signs_out_every_other_device(owner):
    other = browser()
    other.post("/login", data=OWNER)
    assert other.get("/app", follow_redirects=False).status_code == 200
    turn_on(owner)
    assert other.get("/app", follow_redirects=False).status_code == 303
    assert owner.get("/app", follow_redirects=False).status_code == 200
    other.__exit__(None, None, None)


def test_without_an_encryption_key_it_cannot_be_set_up(owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "encryption_key", "")
    page = owner.get("/app/settings/mfa")
    assert page.status_code == 200 and "encryption key" in page.text
    assert user_row().mfa_secret_enc is None


# --- signing in (the acceptance) ----------------------------------------------------------------


def test_an_enrolled_person_cannot_sign_in_without_a_code(owner):
    secret, _codes = turn_on(owner)
    owner.post("/logout")

    halfway = password_step(owner)
    assert halfway.status_code == 303 and halfway.headers["location"] == "/login/code"
    assert owner.get("/app", follow_redirects=False).status_code == 303  # not signed in yet
    assert "Enter your code" in owner.get("/login/code").text

    wrong = owner.post("/login/code", data={"code": "123456"}, follow_redirects=False)
    assert wrong.status_code == 401
    done = owner.post("/login/code", data={"code": code(secret, 1)}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "/app"
    assert owner.get("/app", follow_redirects=False).status_code == 200


def test_a_code_works_only_once(owner):
    secret, _codes = turn_on(owner)
    used = code(secret, 1)
    owner.post("/logout")
    password_step(owner)
    assert owner.post("/login/code", data={"code": used}, follow_redirects=False).status_code == 303
    owner.post("/logout")
    password_step(owner)
    replay = owner.post("/login/code", data={"code": used}, follow_redirects=False)
    assert replay.status_code == 401


def test_a_recovery_code_signs_in_once(owner):
    _secret, codes = turn_on(owner)
    owner.post("/logout")
    password_step(owner)
    done = owner.post("/login/code", data={"code": codes[0].upper()}, follow_redirects=False)
    assert done.headers["location"] == "/app/settings/mfa?used=recovery"
    assert "9 recovery codes left" in owner.get(done.headers["location"]).text

    owner.post("/logout")
    password_step(owner)
    again = owner.post("/login/code", data={"code": codes[0]}, follow_redirects=False)
    assert again.status_code == 401


def test_guessing_codes_is_stopped(owner):
    secret, _codes = turn_on(owner)
    owner.post("/logout")
    password_step(owner)
    for _ in range(5):
        owner.post("/login/code", data={"code": "000000"})
    locked = owner.post("/login/code", data={"code": code(secret, 1)}, follow_redirects=False)
    assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


def test_the_code_step_needs_a_fresh_password_step(owner, monkeypatch):
    turn_on(owner)
    owner.post("/logout")
    assert owner.get("/login/code", follow_redirects=False).headers["location"] == "/login"
    monkeypatch.setattr("app.mfa.CHALLENGE_SECONDS", -1)
    password_step(owner)
    stale = owner.post("/login/code", data={"code": "000000"}, follow_redirects=False)
    assert stale.status_code == 303 and stale.headers["location"] == "/login"


def test_signing_in_with_the_password_does_not_start_a_session_row(owner):
    turn_on(owner)
    owner.post("/logout")
    with SessionLocal() as db:
        before = len(db.scalars(select(UserSession)).all())
    password_step(owner)
    with SessionLocal() as db:
        assert len(db.scalars(select(UserSession)).all()) == before


# --- managing it --------------------------------------------------------------------------------


def test_turning_it_off_needs_the_password_and_a_code(owner):
    secret, _codes = turn_on(owner)
    refused = owner.post(
        "/app/settings/mfa/disable", data={"password": "not-it-at-all", "code": code(secret, 1)}
    )
    assert refused.status_code == 400 and user_row().mfa_enabled_at is not None
    owner.post(
        "/app/settings/mfa/disable", data={"password": OWNER["password"], "code": code(secret, 1)}
    )
    row = user_row()
    assert row.mfa_enabled_at is None
    # The page offers a fresh setup afterwards; the old secret is gone either way.
    assert row.mfa_secret_enc is None or decrypt(row.mfa_secret_enc) != secret
    with SessionLocal() as db:
        assert db.scalars(select(RecoveryCode)).all() == []
    owner.post("/logout")
    assert password_step(owner).headers["location"] == "/app"


def test_new_recovery_codes_replace_the_old_ones(owner):
    secret, old = turn_on(owner)
    page = owner.post("/app/settings/mfa/recovery-codes", data={"code": code(secret, 1)})
    new = CODES.findall(page.text)
    assert len(new) == 10 and not set(new) & set(old)
    owner.post("/logout")
    password_step(owner)
    assert owner.post("/login/code", data={"code": old[0]}).status_code == 401
    assert owner.post("/login/code", data={"code": new[0]}).status_code == 200


def test_owners_are_prompted_until_it_is_on(owner):
    assert "protect it with two-step sign-in" in owner.get("/app").text
    assert "protect it with two-step sign-in" not in owner.get("/app/settings/mfa").text
    page = owner.get("/app/settings/mfa").text
    secret = SETUP_KEY.search(page).group(1).replace(" ", "")
    done = owner.post("/app/settings/mfa/enable", data={"code": code(secret)})
    assert "protect it with two-step sign-in" not in done.text
    assert "protect it with two-step sign-in" not in owner.get("/app").text


def test_support_can_turn_it_off_for_a_locked_out_person(owner):
    turn_on(owner)
    assert cmd_reset_mfa(argparse.Namespace(email=OWNER["email"])) == 0
    assert user_row().mfa_enabled_at is None
    assert owner.get("/app", follow_redirects=False).status_code == 303  # sessions ended


def test_an_invitation_never_signs_in_an_enrolled_account_by_password(owner):
    other = browser()
    baker = {"business_name": "Bakery", "email": "baker@x.test", "password": OWNER["password"]}
    other.post("/signup", data=baker)
    confirm_email("baker@x.test")
    baker_secret, _ = turn_on(other)
    other.post("/logout")
    with pytest.MonkeyPatch.context() as patch:
        sent = []
        patch.setattr("app.team.send_email", lambda *a, **k: sent.append(a) or True)
        owner.post("/app/team/invite", data={"email": "baker@x.test", "role": "employee"})
    token = sent[0][6].split("token=", 1)[1].split()[0]  # the plain-text part
    page = other.get("/invitations/accept", params={"token": token})
    assert "two-step sign-in" in page.text and 'name="password"' not in page.text
    refused = other.post(
        "/invitations/accept", data={"token": token, "password": OWNER["password"]}
    )
    assert refused.status_code == 403
    other.__exit__(None, None, None)
    assert baker_secret


# --- the rules, at fixed times ------------------------------------------------------------------


def test_a_code_is_good_for_its_step_and_one_either_side_and_never_twice():
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    noon = datetime(2026, 11, 11, 12, 0, 15, tzinfo=UTC)
    step = totp.timecode(noon)
    assert matching_step(secret, totp.generate_otp(step), noon, None) == step
    assert matching_step(secret, totp.generate_otp(step - 1), noon, None) == step - 1
    assert matching_step(secret, totp.generate_otp(step + 1), noon, None) == step + 1
    assert matching_step(secret, totp.generate_otp(step - 2), noon, None) is None
    assert matching_step(secret, totp.generate_otp(step + 2), noon, None) is None
    assert matching_step(secret, totp.generate_otp(step), noon, step) is None  # replay
    assert matching_step(secret, totp.generate_otp(step - 1), noon, step - 1) is None
    later = noon + timedelta(seconds=30)
    assert matching_step(secret, totp.generate_otp(step + 1), later, step) == step + 1
    assert matching_step(secret, "12 34 5a", noon, None) is None


def test_recovery_codes_are_random_readable_and_hashed_the_same_however_typed():
    codes = new_recovery_codes()
    assert len(set(codes)) == 10
    assert all(re.fullmatch(r"[a-z2-9]{5}-[a-z2-9]{5}", c) for c in codes)
    assert recovery_hash("ABCDE-FGHJK") == recovery_hash("abcde fghjk")
