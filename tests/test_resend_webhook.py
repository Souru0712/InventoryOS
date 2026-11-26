"""ADR-0088: Resend's hard bounces and spam complaints suppress the address; nothing else does."""

import base64
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import EmailSuppression
from app.notify.resend import Unverified, sign, signing_key, verify

SECRET = "whsec_" + base64.b64encode(b"resend-test-signing-key-32-bytes").decode()
ADDRESS = "gone@example.test"


@pytest.fixture
def resend(db, monkeypatch):
    monkeypatch.setattr(get_settings(), "resend_webhook_secret", SECRET, raising=False)
    with TestClient(app) as client:
        yield client


def event(kind="email.bounced", to=(ADDRESS,), bounce_type="Permanent", email_id="em-1"):
    data = {"email_id": email_id, "from": "Vontry <alerts@vontry.app>", "to": list(to)}
    if kind == "email.bounced":
        data["bounce"] = {
            "type": bounce_type,
            "subType": "General",
            "message": f"550 5.1.1 <{to[0]}>: mailbox unavailable",
        }
    return {"type": kind, "created_at": "2026-11-26T12:00:00.000Z", "data": data}


def deliver(client, payload, *, msg_id="msg_1", sent=None, signature=None, secret=SECRET):
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()) if sent is None else sent)
    if signature is None:
        signature = "v1," + sign(signing_key(secret), msg_id, timestamp, body)
    headers = {"svix-id": msg_id, "svix-timestamp": timestamp, "svix-signature": signature}
    return client.post("/webhooks/resend", content=body, headers=headers)


def suppressed():
    with SessionLocal() as db:
        rows = db.scalars(select(EmailSuppression).order_by(EmailSuppression.email))
        return [(row.email, row.source, row.reason) for row in rows]


def test_the_signature_matches_svix_reference_vector():
    """Svix's published example, checked at its own moment: the stdlib scheme is theirs."""
    verify(
        "whsec_plJ3nmyCDGBKInavdOK15jsl",
        "msg_loFOjxBNrRLzqYUf",
        "1731705121",
        "v1,rAvfW3dJ/X/qxhsaXPOyyCGmRKsaKWcsNccKXlIktD0=",
        b'{"event_type":"ping","data":{"success":true}}',
        now=1731705121,
    )


def test_a_signed_hard_bounce_suppresses_the_address(resend, caplog):
    caplog.set_level(logging.INFO)
    response = deliver(resend, event(to=("Gone@Example.test",)))

    assert response.status_code == 200
    assert response.json() == {"suppressed": 1}
    [(email, source, reason)] = suppressed()
    assert (email, source) == (ADDRESS, "bounce")
    assert "em-1" in reason and "@" not in reason
    assert ADDRESS not in caplog.text.lower(), "logs carry ids, never the address"


@pytest.mark.parametrize("kind", ["Transient", "Temporary", "Undetermined", ""])
def test_a_soft_bounce_suppresses_nothing(resend, kind):
    response = deliver(resend, event(bounce_type=kind))

    assert response.status_code == 200
    assert response.json() == {"suppressed": 0}
    assert suppressed() == []


def test_a_complaint_suppresses_as_a_complaint(resend):
    response = deliver(resend, event("email.complained", to=(ADDRESS, "other@example.test")))

    assert response.status_code == 200
    assert [(email, source) for email, source, _ in suppressed()] == [
        (ADDRESS, "complaint"),
        ("other@example.test", "complaint"),
    ]


def test_a_repeated_delivery_changes_nothing(resend):
    deliver(resend, event())
    again = deliver(resend, event())

    assert again.status_code == 200
    assert len(suppressed()) == 1


def test_other_events_are_acknowledged_and_ignored(resend):
    response = deliver(resend, {"type": "email.delivered", "data": {"to": [ADDRESS]}})

    assert response.status_code == 200
    assert suppressed() == []


def test_one_matching_signature_among_several_is_enough(resend):
    payload = event()
    body = json.dumps(payload).encode()
    now = str(int(time.time()))
    good = sign(signing_key(SECRET), "msg_2", now, body)
    response = resend.post(
        "/webhooks/resend",
        content=body,
        headers={
            "svix-id": "msg_2",
            "svix-timestamp": now,
            "svix-signature": f"v1,bm9wZQ== v2,{good} v1,{good}",
        },
    )

    assert response.status_code == 200
    assert len(suppressed()) == 1


@pytest.mark.parametrize(
    "tamper",
    [
        {"signature": "v1,Zm9yZ2Vk"},
        {"signature": ""},
        {"secret": "whsec_" + base64.b64encode(b"someone-else").decode()},
        {"skew": -301},
        {"skew": 301},
        {"sent": "yesterday"},
        {"msg_id": ""},
    ],
)
def test_an_unproven_call_is_refused_and_writes_nothing(resend, tamper):
    tamper = dict(tamper)
    if "skew" in tamper:
        tamper["sent"] = int(time.time()) + tamper.pop("skew")
    response = deliver(resend, event(), **tamper)

    assert response.status_code == 400
    assert suppressed() == []


def test_a_body_changed_after_signing_is_refused(resend):
    body = json.dumps(event()).encode()
    now = str(int(time.time()))
    signature = "v1," + sign(signing_key(SECRET), "msg_3", now, body)
    response = resend.post(
        "/webhooks/resend",
        content=body.replace(b"em-1", b"em-2"),
        headers={"svix-id": "msg_3", "svix-timestamp": now, "svix-signature": signature},
    )

    assert response.status_code == 400
    assert suppressed() == []


@pytest.mark.parametrize("secret", ["", "whsec_not base64!"])
def test_without_a_usable_secret_everything_is_refused(resend, monkeypatch, secret):
    monkeypatch.setattr(get_settings(), "resend_webhook_secret", secret, raising=False)
    response = deliver(resend, event())

    assert response.status_code == 503
    assert suppressed() == []


def test_a_stale_timestamp_is_unverified_even_when_signed():
    body = b"{}"
    signature = "v1," + sign(signing_key(SECRET), "msg_4", "1000", body)
    with pytest.raises(Unverified, match="too far"):
        verify(SECRET, "msg_4", "1000", signature, body, now=1000 + 301)
    verify(SECRET, "msg_4", "1000", signature, body, now=1000 + 300)
