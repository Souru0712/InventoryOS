"""Square OAuth: authorize URL, code exchange, refresh, revocation (T3.02).

Tokens are stored encrypted by `app/connections.py` through `app/crypto.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.config import get_settings

# What the product reads: merchant profile, catalog and orders (SQ-02). Inventory only while
# write-back is on (R1.1, C-34): Square's counts never change stock, so reading them serves
# nothing else.
BASE_SCOPES = ("MERCHANT_PROFILE_READ", "ITEMS_READ", "ORDERS_READ")
WRITE_BACK_SCOPES = ("INVENTORY_READ", "INVENTORY_WRITE")


def scopes() -> tuple[str, ...]:
    return BASE_SCOPES + (WRITE_BACK_SCOPES if get_settings().square_write_back else ())


class OAuthError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def authorize_url(state: str) -> str:
    settings = get_settings()
    if not settings.square_app_id:
        raise OAuthError("SQUARE_APP_ID is not set")
    query = urlencode(
        {
            "client_id": settings.square_app_id,
            "scope": " ".join(scopes()),
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
            f"square token request failed ({response.status_code}): {response.text[:500]}",
            response.status_code,
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


def revoke(access_token: str) -> None:
    """Revoke the merchant's authorization of this application at Square.

    Square revokes every token the merchant granted the application, not only this one.
    """
    settings = get_settings()
    response = httpx.post(
        f"{settings.square_api_base}/oauth2/revoke",
        json={"client_id": settings.square_app_id, "access_token": access_token},
        headers={
            "Authorization": f"Client {settings.square_app_secret}",
            "Square-Version": settings.square_api_version,
        },
        timeout=30.0,
    )
    if response.status_code >= 400:
        raise OAuthError(
            f"square revoke failed ({response.status_code}): {response.text[:500]}",
            response.status_code,
        )


def _normalize_token(data: dict) -> dict:
    expires_at = data.get("expires_at")
    parsed: datetime | None = None
    if expires_at:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        # Square's stamps carry an offset; one without is read as UTC (columns refuse naive).
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "merchant_id": data.get("merchant_id", ""),
        "expires_at": parsed or datetime.now(UTC) + timedelta(days=30),
    }
