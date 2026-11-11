"""Secrets kept encrypted at rest with the application key (ENCRYPTION_KEY, Fernet)."""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class CryptoUnavailable(RuntimeError):
    """No key to encrypt with, or a stored value the current key cannot open."""


def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise CryptoUnavailable("ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise CryptoUnavailable(
            "a stored secret could not be decrypted; was ENCRYPTION_KEY changed?"
        ) from exc
