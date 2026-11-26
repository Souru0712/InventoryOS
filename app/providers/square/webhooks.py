"""Square webhook signatures and the event types Vontry acts on."""

from __future__ import annotations

import base64
import hashlib
import hmac

CATALOG_VERSION_UPDATED = "catalog.version.updated"
# The merchant removed the application in Square (SQ-01): the connection is over.
OAUTH_AUTHORIZATION_REVOKED = "oauth.authorization.revoked"
# An order created, changed or fulfilled: a hint to read the changed orders (INT-P-05).
ORDER_EVENTS = frozenset({"order.created", "order.updated", "order.fulfillment.updated"})


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


def merchant_id(event: dict) -> str:
    return event.get("merchant_id") or ""
