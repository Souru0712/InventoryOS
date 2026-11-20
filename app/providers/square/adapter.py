"""Square as a POS adapter (T3.01): the contract in `app/providers/base.py`, implemented with
the existing client, OAuth and webhook code. Everything Square-shaped stays in this package."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.providers.base import (
    AdapterError,
    Catalog,
    Credentials,
    NormalizedEvent,
    NormalizedLocation,
    OrderPage,
    Unsupported,
)
from app.providers.square import oauth
from app.providers.square.client import SquareClient, SquareError
from app.providers.square.normalize import (
    to_event,
    to_location,
    to_modifiers,
    to_order,
    to_products,
)
from app.providers.square.webhooks import verify_signature

SIGNATURE_HEADER = "x-square-hmacsha256-signature"


def _credentials(token: dict) -> Credentials:
    return Credentials(
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        expires_at=token.get("expires_at") or datetime.now(UTC) + timedelta(days=30),
        external_merchant_id=token.get("merchant_id") or "",
    )


class SquareAdapter:
    provider = "square"

    def authorize_url(self, state: str) -> str:
        return oauth.authorize_url(state)

    def exchange_code(self, code: str) -> Credentials:
        try:
            return _credentials(oauth.exchange_code(code))
        except oauth.OAuthError as exc:
            raise AdapterError(str(exc)) from exc

    def refresh(self, credentials: Credentials) -> Credentials:
        if not credentials.refresh_token:
            raise AdapterError("this connection has no refresh token")
        try:
            return _credentials(oauth.refresh(credentials.refresh_token))
        except oauth.OAuthError as exc:
            raise AdapterError(str(exc)) from exc

    def revoke(self, credentials: Credentials) -> None:
        raise Unsupported(
            "revoking a Square connection arrives with the connection lifecycle (T3.02)"
        )

    def list_locations(self, credentials: Credentials) -> list[NormalizedLocation]:
        with SquareClient(credentials.access_token) as client:
            try:
                raw = client.locations()
            except SquareError as exc:
                raise AdapterError(str(exc)) from exc
        return [to_location(location) for location in raw if location.get("id")]

    def sync_catalog(self, credentials: Credentials, since: datetime | None = None) -> Catalog:
        with SquareClient(credentials.access_token) as client:
            try:
                raw = client.catalog_objects(("ITEM", "MODIFIER_LIST", "CATEGORY"))
            except SquareError as exc:
                raise AdapterError(str(exc)) from exc
        return Catalog(to_products(raw), to_modifiers(raw))

    def fetch_orders(
        self,
        credentials: Credentials,
        location_ids: list[str],
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> OrderPage:
        """Completed orders closed in the window; the client follows Square's cursor itself."""
        with SquareClient(credentials.access_token) as client:
            try:
                raw = client.orders(location_ids, since, until)
            except SquareError as exc:
                raise AdapterError(str(exc)) from exc
        return OrderPage(tuple(to_order(order) for order in raw if order.get("id")))

    def parse_webhook(
        self, raw: bytes, headers: Mapping[str, str], url: str
    ) -> NormalizedEvent | None:
        """A verified event, or None when the signature does not match."""
        key = get_settings().square_webhook_signature_key
        signature = next((v for k, v in headers.items() if k.lower() == SIGNATURE_HEADER), "")
        if not verify_signature(url, raw, signature, key):
            return None
        return to_event(json.loads(raw or b"{}"))
