"""Square OAuth: authorize URL, code exchange, refresh, token encryption."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

SCOPES = [
    "MERCHANT_PROFILE_READ",
    "ITEMS_READ",
    "INVENTORY_READ",
    "INVENTORY_WRITE",
    "ORDERS_READ",
    # Snack boxes: a hosted checkout per order, and the payment/refund webhooks that follow it.
    "ORDERS_WRITE",
    "PAYMENTS_READ",
    "PAYMENTS_WRITE",
]


class OAuthError(RuntimeError):
    pass


def _fernet() -> Fernet:
    settings = get_settings()
    if not settings.encryption_key:
        raise OAuthError(
            "ENCRYPTION_KEY is not set; generate one with "
            'python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.encryption_key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise OAuthError(
            "stored token could not be decrypted; ENCRYPTION_KEY may have changed"
        ) from exc


def authorize_url(state: str) -> str:
    settings = get_settings()
    if not settings.square_app_id:
        raise OAuthError("SQUARE_APP_ID is not set")
    query = urlencode(
        {
            "client_id": settings.square_app_id,
            "scope": " ".join(SCOPES),
            "session": "false",
            "state": state,
            "redirect_uri": f"{settings.app_base_url}/connect/square/callback",
        }
    )
    return f"{settings.square_api_base}/oauth2/authorize?{query}"


def _token_request(payload: dict) -> dict:
    settings = get_settings()
    response = httpx.post(
        f"{settings.square_api_base}/oauth2/token",
        json=payload
        | {
            "client_id": settings.square_app_id,
            "client_secret": settings.square_app_secret,
        },
        headers={"Square-Version": settings.square_api_version},
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise OAuthError(
            f"square token exchange failed ({response.status_code}): {response.text[:500]}"
        )
    return response.json()


def exchange_code(code: str) -> dict:
    settings = get_settings()
    data = _token_request(
        {
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": f"{settings.app_base_url}/connect/square/callback",
        }
    )
    return _normalize_token(data)


def refresh(refresh_token: str) -> dict:
    return _normalize_token(
        _token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})
    )


def _normalize_token(data: dict) -> dict:
    expires_at = data.get("expires_at")
    parsed: datetime | None = None
    if expires_at:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "merchant_id": data.get("merchant_id", ""),
        "expires_at": parsed or datetime.now(UTC) + timedelta(days=30),
    }
