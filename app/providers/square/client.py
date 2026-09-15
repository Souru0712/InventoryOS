"""Square REST client. Ingestion and write-back only, no transform logic."""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Iterator

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
BATCH_SIZE = 250


class SquareError(RuntimeError):
    """Square API failure with the context needed to debug it."""

    def __init__(self, method: str, path: str, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"square {method} {path} -> {status}: {body[:500]}")


class SquareClient:
    def __init__(self, access_token: str, *, timeout: float = 30.0):
        settings = get_settings()
        self._base = settings.square_api_base
        self._client = httpx.Client(
            base_url=self._base,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Square-Version": settings.square_api_version,
                "Content-Type": "application/json",
            },
        )

    def __enter__(self) -> SquareClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            response = self._client.request(method, path, **kwargs)
            if response.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS:
                backoff = 2 ** (attempt - 1)
                log.warning(
                    "square %s %s returned %s, retry %s/%s in %ss",
                    method,
                    path,
                    response.status_code,
                    attempt,
                    MAX_ATTEMPTS,
                    backoff,
                )
                time.sleep(backoff)
                continue
            if response.status_code >= 400:
                raise SquareError(method, path, response.status_code, response.text)
            return response.json()
        raise SquareError(method, path, response.status_code, response.text)

    def _paginate(self, method: str, path: str, body: dict | None, key: str) -> Iterator[dict]:
        cursor: str | None = None
        while True:
            if method == "GET":
                params = {"cursor": cursor} if cursor else None
                payload = self._request(method, path, params=params)
            else:
                request_body = dict(body or {})
                if cursor:
                    request_body["cursor"] = cursor
                payload = self._request(method, path, json=request_body)

            yield from payload.get(key, []) or []
            cursor = payload.get("cursor")
            if not cursor:
                return

    # --- reads ---

    def merchant(self) -> dict:
        return self._request("GET", "/v2/merchants/me").get("merchant", {})

    def locations(self) -> list[dict]:
        return self._request("GET", "/v2/locations").get("locations", []) or []

    def catalog_objects(self, types: tuple[str, ...] = ("ITEM",)) -> list[dict]:
        """Items with their variations attached as related objects."""
        body = {"object_types": list(types), "include_related_objects": True, "limit": 200}
        return list(self._paginate("POST", "/v2/catalog/search", body, "objects"))

    def inventory_counts(
        self, catalog_object_ids: list[str], location_ids: list[str]
    ) -> list[dict]:
        counts: list[dict] = []
        for start in range(0, len(catalog_object_ids), BATCH_SIZE):
            chunk = catalog_object_ids[start : start + BATCH_SIZE]
            body = {"catalog_object_ids": chunk, "location_ids": location_ids}
            counts.extend(self._paginate("POST", "/v2/inventory/counts/batch-retrieve", body, "counts"))
        return counts

    def orders(self, location_ids: list[str], start_at: datetime, end_at: datetime) -> list[dict]:
        body = {
            "location_ids": location_ids,
            "limit": 200,
            "query": {
                "filter": {
                    "date_time_filter": {
                        "closed_at": {
                            "start_at": _rfc3339(start_at),
                            "end_at": _rfc3339(end_at),
                        }
                    },
                    "state_filter": {"states": ["COMPLETED"]},
                },
                "sort": {"sort_field": "CLOSED_AT", "sort_order": "ASC"},
            },
        }
        return list(self._paginate("POST", "/v2/orders/search", body, "orders"))

    # --- write-back ---

    def apply_inventory_changes(self, changes: list[dict], idempotency_key: str) -> dict:
        """Push physical counts or adjustments so the online store stops overselling."""
        if not changes:
            return {}
        return self._request(
            "POST",
            "/v2/inventory/changes/batch-create",
            json={"idempotency_key": idempotency_key, "changes": changes},
        )

    def create_payment_link(
        self,
        *,
        idempotency_key: str,
        name: str,
        amount_cents: int,
        currency: str,
        location_id: str,
        redirect_url: str,
        note: str,
        buyer_email: str | None = None,
    ) -> dict:
        """A Square-hosted checkout for one amount. Its order_id is what the payment webhook carries."""
        body: dict = {
            "idempotency_key": idempotency_key,
            "quick_pay": {
                "name": name,
                "price_money": {"amount": amount_cents, "currency": currency},
                "location_id": location_id,
            },
            "checkout_options": {"redirect_url": redirect_url},
            "payment_note": note[:500],
        }
        if buyer_email:
            body["pre_populated_data"] = {"buyer_email": buyer_email}
        return self._request("POST", "/v2/online-checkout/payment-links", json=body).get("payment_link") or {}


def physical_count_change(
    catalog_object_id: str, location_id: str, quantity: str, occurred_at: datetime
) -> dict:
    """Shape one PHYSICAL_COUNT change for batch-create."""
    return {
        "type": "PHYSICAL_COUNT",
        "physical_count": {
            "catalog_object_id": catalog_object_id,
            "location_id": location_id,
            "state": "IN_STOCK",
            "quantity": quantity,
            "occurred_at": _rfc3339(occurred_at),
        },
    }


def adjustment_change(
    catalog_object_id: str, location_id: str, quantity: str, occurred_at: datetime
) -> dict:
    """Shape one ADJUSTMENT for batch-create: a delta rather than an absolute count.

    Receiving adds to whatever Square holds now, so a sale that lands between the scan and
    the write is not silently overwritten the way a physical count would overwrite it.
    """
    return {
        "type": "ADJUSTMENT",
        "adjustment": {
            "catalog_object_id": catalog_object_id,
            "location_id": location_id,
            "from_state": "NONE",
            "to_state": "IN_STOCK",
            "quantity": quantity,
            "occurred_at": _rfc3339(occurred_at),
        },
    }

def state_change(
    catalog_object_id: str,
    location_id: str,
    quantity: str,
    occurred_at: datetime,
    from_state: str,
    to_state: str,
) -> dict:
    """Shape one ADJUSTMENT between two states, e.g. IN_STOCK -> SOLD when a paid box takes its units."""
    return {
        "type": "ADJUSTMENT",
        "adjustment": {
            "catalog_object_id": catalog_object_id,
            "location_id": location_id,
            "from_state": from_state,
            "to_state": to_state,
            "quantity": quantity,
            "occurred_at": _rfc3339(occurred_at),
        },
    }


def _rfc3339(value: datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")
