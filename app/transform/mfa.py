"""Two-step sign-in codes (SAAS-A-07): authenticator (TOTP) codes and recovery codes. Pure.

A TOTP code is accepted for the current 30-second step and one step either side, for clocks
that drift, and never for a step at or before the last one used, so a code read over someone's
shoulder cannot be replayed. Recovery codes are random, shown once, and stored only as hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

import pyotp

ISSUER = "StockPulse"
DRIFT = 1  # steps either side of now
RECOVERY_CODES = 10
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i: read aloud and typed by hand
# Owners and Administrators are prompted to turn it on (PD-20 → C-44: optional, strong prompt).
PROMPTED_ROLES = frozenset({"owner", "administrator"})


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    """What the QR code holds: the account name and issuer an authenticator app shows."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def grouped(secret: str) -> str:
    """The setup key in groups of four, for typing it in by hand."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


def normalize(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def matching_step(secret: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """The time step a six-digit code belongs to, or None if it is wrong, stale or used."""
    code = normalize(code)
    if len(code) != 6 or not code.isdigit():
        return None
    totp = pyotp.TOTP(secret)
    current = totp.timecode(now)
    for step in range(current - DRIFT, current + DRIFT + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def new_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """Ten single-use codes like "k7m2p-x9qrt" (about 49 bits each)."""
    return [
        "-".join("".join(secrets.choice(_ALPHABET) for _ in range(5)) for _ in range(2))
        for _ in range(count)
    ]


def recovery_hash(code: str) -> str:
    return hashlib.sha256(normalize(code).encode()).hexdigest()
