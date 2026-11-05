"""Square webhook signatures, and reading an inventory event off the payload."""

from __future__ import annotations

import base64
import hashlib
import hmac

INVENTORY_COUNT_UPDATED = "inventory.count.updated"
CATALOG_VERSION_UPDATED = "catalog.version.updated"
PAYMENT_UPDATED = "payment.updated"
REFUND_EVENTS = ("refund.created", "refund.updated")


def signature_for(notification_url: str, body: bytes, signature_key: str) -> str:
    """Square signs the notification URL concatenated with the raw body, not the body alone."""
    payload = notification_url.encode() + body
    digest = hmac.new(signature_key.encode(), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_signature(
    notification_url: str, body: bytes, signature: str, signature_key: str
) -> bool:
    """Constant-time comparison: a timing leak here would let someone forge stock changes."""
    if not signature or not signature_key:
        return False
    expected = signature_for(notification_url, body, signature_key)
    return hmac.compare_digest(expected, signature)


def inventory_counts(event: dict) -> list[dict]:
    """The counts carried by an inventory.count.updated event, in Square's own shape."""
    if event.get("type") != INVENTORY_COUNT_UPDATED:
        return []
    obj = ((event.get("data") or {}).get("object")) or {}
    return obj.get("inventory_counts") or []


def merchant_id(event: dict) -> str:
    return event.get("merchant_id") or ""
