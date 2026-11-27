"""Resend's word on mail it could not deliver (ADR-0088): bounces and complaints suppress.

Resend signs each webhook the Svix way: an HMAC-SHA256 of `{svix-id}.{svix-timestamp}.{body}`,
keyed with the base64 part of the `whsec_` secret, sent as space-separated `v1,<base64>` entries
in `svix-signature`. A call more than five minutes off our clock is refused, so a captured
delivery cannot be replayed later. Only a permanent bounce suppresses: a full mailbox or a slow
server is Resend's to retry, and suppressing it would cut off someone whose address works.
Suppressing is safe to repeat (`suppress()` updates the existing row), so Resend's retries and
replays change nothing and no event store is needed. Suppressions belong to no organization, so
this module writes them through `app/notify/email.py` as the CLI does.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import time
from email.utils import parseaddr
from typing import Any

from sqlalchemy.orm import Session

from app.notify.email import suppress

log = logging.getLogger(__name__)

TOLERANCE_SECONDS = 5 * 60
BOUNCED, COMPLAINED = "email.bounced", "email.complained"
SECRET_PREFIX = "whsec_"  # noqa: S105 - the format's prefix, not a secret


class Unverified(ValueError):
    """The call does not prove it comes from Resend now."""


def signing_key(secret: str) -> bytes:
    """The HMAC key inside a `whsec_` secret; a malformed secret is a configuration error."""
    try:
        key = base64.b64decode(secret.removeprefix(SECRET_PREFIX), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("RESEND_WEBHOOK_SECRET is not a whsec_ signing secret") from exc
    if not key:
        raise ValueError("RESEND_WEBHOOK_SECRET is not a whsec_ signing secret")
    return key


def sign(key: bytes, msg_id: str, timestamp: str, body: bytes) -> str:
    """The base64 v1 signature over one delivery."""
    content = msg_id.encode() + b"." + timestamp.encode() + b"." + body
    return base64.b64encode(hmac.new(key, content, hashlib.sha256).digest()).decode()


def verify(
    secret: str,
    msg_id: str,
    timestamp: str,
    signatures: str,
    body: bytes,
    now: float | None = None,
) -> None:
    """Raise `Unverified` unless one `v1` signature matches and the timestamp is fresh."""
    key = signing_key(secret)
    if not (msg_id and timestamp and signatures):
        raise Unverified("missing svix headers")
    try:
        sent = int(timestamp)
    except ValueError as exc:
        raise Unverified("the timestamp is not a number") from exc
    if abs((time.time() if now is None else now) - sent) > TOLERANCE_SECONDS:
        raise Unverified("the timestamp is too far from now")
    expected = sign(key, msg_id, timestamp, body).encode()
    for entry in signatures.split():
        version, _, signature = entry.partition(",")
        if version == "v1" and hmac.compare_digest(signature.encode(), expected):
            return
    raise Unverified("no signature matches")


def _recipients(data: dict[str, Any]) -> list[str]:
    to = data.get("to")
    listed = [to] if isinstance(to, str) else to if isinstance(to, list) else []
    addresses = [parseaddr(item)[1] for item in listed if isinstance(item, str)]
    return [address for address in addresses if "@" in address]


def suppressions(event: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(address, source, reason) for each recipient this event says to stop mailing.

    The reason names the bounce kind and Resend's email id, never the provider's message: that
    often quotes the address, and `suppress()` logs its reason.
    """
    data = event.get("data")
    if not isinstance(data, dict):
        return []
    email_id = str(data.get("email_id") or "unknown")[:64]
    kind = event.get("type")
    if kind == BOUNCED:
        bounce = data.get("bounce")
        bounce = bounce if isinstance(bounce, dict) else {}
        if str(bounce.get("type", "")).lower() != "permanent":
            return []
        sub_type = str(bounce.get("subType") or "General")[:40]
        reason = f"Resend: permanent bounce ({sub_type}), email {email_id}"
        return [(address, "bounce", reason) for address in _recipients(data)]
    if kind == COMPLAINED:
        reason = f"Resend: marked as spam, email {email_id}"
        return [(address, "complaint", reason) for address in _recipients(data)]
    return []


def apply_event(db: Session, event: dict[str, Any], delivery_id: str) -> int:
    """Suppress what a verified event names; the number of addresses suppressed."""
    found = suppressions(event)
    for address, source, reason in found:
        suppress(db, address, reason, source)
    data = event.get("data")
    email_id = data.get("email_id") if isinstance(data, dict) else None
    # Ids and the type only: never the address or the body (CLAUDE.md logging rules).
    log.info(
        "resend %s delivery %s for email %s: %d suppressed",
        str(event.get("type"))[:40],
        delivery_id[:64],
        str(email_id)[:64],
        len(found),
    )
    return len(found)
