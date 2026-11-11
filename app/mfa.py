"""Two-step sign-in for a person (T1.11, SAAS-A-07): enrolling, checking codes, the sign-in step.

The authenticator secret is encrypted with the application key; recovery codes are stored as
hashes and work once. After a correct password, a person with two-step sign-in on holds only a
short-lived "password passed" mark in the signed cookie until a code completes the sign-in.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

import segno
from fastapi import Request
from markupsafe import Markup
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.crypto import decrypt, encrypt
from app.models import RecoveryCode, User
from app.transform.mfa import (
    matching_step,
    new_recovery_codes,
    new_secret,
    normalize,
    provisioning_uri,
    recovery_hash,
)

log = logging.getLogger(__name__)

PENDING_KEY = "mfa_pending"
CHALLENGE_SECONDS = 300


# --- enrolling ----------------------------------------------------------------------------------


def enrolment_secret(db: Session, user: User) -> str:
    """The secret being enrolled: kept across page loads until confirmed, so a QR code already
    scanned stays good. Raises CryptoUnavailable without an ENCRYPTION_KEY."""
    if user.mfa_enabled_at is None and user.mfa_secret_enc:
        return decrypt(user.mfa_secret_enc)
    secret = new_secret()
    user.mfa_secret_enc = encrypt(secret)
    user.mfa_last_step = None
    db.flush()
    return secret


def qr_svg(secret: str, email: str) -> Markup:
    """The enrolment QR code as inline SVG (no script, no image request)."""
    qr = segno.make(provisioning_uri(secret, email), error="m")
    return Markup(qr.svg_inline(scale=4, light="#ffffff", border=2))  # noqa: S704 (segno's own SVG)


def _set_recovery_codes(db: Session, user: User) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = new_recovery_codes()
    db.add_all(RecoveryCode(user_id=user.id, code_hash=recovery_hash(code)) for code in codes)
    db.flush()
    return codes


def enable(db: Session, user: User, code: str) -> list[str] | None:
    """Turn two-step sign-in on if code matches the secret being enrolled; the new recovery
    codes, to show once, or None if the code is wrong."""
    if user.mfa_enabled_at is not None or not user.mfa_secret_enc:
        return None
    step = matching_step(decrypt(user.mfa_secret_enc), code, datetime.now(UTC), None)
    if step is None:
        return None
    user.mfa_last_step = step
    user.mfa_enabled_at = datetime.now(UTC)
    log.info("user %s turned on two-step sign-in", user.id)
    return _set_recovery_codes(db, user)


def disable(db: Session, user: User) -> None:
    user.mfa_secret_enc = None
    user.mfa_enabled_at = None
    user.mfa_last_step = None
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    log.info("user %s turned off two-step sign-in", user.id)


def regenerate_recovery_codes(db: Session, user: User) -> list[str]:
    """New codes; the earlier ones stop working."""
    log.info("user %s replaced their recovery codes", user.id)
    return _set_recovery_codes(db, user)


def recovery_codes_left(db: Session, user: User) -> int:
    query = select(func.count(RecoveryCode.id)).where(
        RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None)
    )
    return int(db.scalar(query) or 0)


# --- checking a code ----------------------------------------------------------------------------


def check_code(db: Session, user: User, code: str) -> str | None:
    """ "authenticator" or "recovery" when code is good (using it up), else None."""
    if user.mfa_enabled_at is None or not user.mfa_secret_enc:
        return None
    now = datetime.now(UTC)
    step = matching_step(decrypt(user.mfa_secret_enc), code, now, user.mfa_last_step)
    if step is not None:
        user.mfa_last_step = step
        return "authenticator"
    if len(normalize(code)) == 10:
        row = db.scalars(
            select(RecoveryCode).where(
                RecoveryCode.user_id == user.id,
                RecoveryCode.code_hash == recovery_hash(code),
                RecoveryCode.used_at.is_(None),
            )
        ).first()
        if row is not None:
            row.used_at = now
            log.info("user %s used a recovery code", user.id)
            return "recovery"
    return None


# --- the sign-in step ---------------------------------------------------------------------------


def start_challenge(request: Request, user: User) -> None:
    """The password was right: remember that, briefly, until a code finishes the sign-in."""
    request.session[PENDING_KEY] = {"user": user.id, "until": int(time.time()) + CHALLENGE_SECONDS}


def challenged_user(request: Request, db: Session) -> User | None:
    """The person halfway through signing in on this browser, if the mark is still fresh."""
    pending = request.session.get(PENDING_KEY)
    if not isinstance(pending, dict) or int(pending.get("until", 0)) < time.time():
        request.session.pop(PENDING_KEY, None)
        return None
    user = db.get(User, int(pending.get("user", 0)))
    if user is None or not user.is_active or user.mfa_enabled_at is None:
        request.session.pop(PENDING_KEY, None)
        return None
    return user
