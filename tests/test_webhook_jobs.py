"""T3.06: webhooks stored once and processed as jobs (POS-FR-11, DR-21, SEC-11, PERF-07,
AC-POS-03, AC-POS-10). Square is a fake adapter; nothing reaches the network."""

import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import pos_catalog
from app.config import get_settings
from app.db import SessionLocal
from app.ledger.posting import balance
from app.main import app
from app.models import ExternalProduct, Job, SalesTransaction, WebhookEvent
from app.order_ingestion import ingest_orders
from app.providers.base import AdapterError, Catalog, NormalizedProduct
from app.providers.square.webhooks import signature_for
from app.webhook_events import processed_by
from tests.test_order_ingestion import NOW, _jobs, latte, order, shop  # noqa: F401

KEY = "test-signature-key"
URL = "https://app.test/webhooks/square"
D = Decimal


@pytest.fixture
def square(shop, monkeypatch):  # noqa: F811
    settings = get_settings()
    monkeypatch.setattr(settings, "square_webhook_signature_key", KEY, raising=False)
    monkeypatch.setattr(settings, "app_base_url", "https://app.test", raising=False)
    with TestClient(app) as client:
        yield client, shop


def deliver(client, event_id, kind="order.updated", merchant="M-1", signature=None):
    body = json.dumps(
        {"event_id": event_id, "type": kind, "merchant_id": merchant, "data": {"id": "o-1"}}
    ).encode()
    return client.post(
        "/webhooks/square",
        content=body,
        headers={"x-square-hmacsha256-signature": signature or signature_for(URL, body, KEY)},
    )


def events() -> list[WebhookEvent]:
    with SessionLocal() as db:
        return list(db.scalars(select(WebhookEvent).order_by(WebhookEvent.id)))


def jobs(kind: str) -> list[Job]:
    with SessionLocal() as db:
        return list(db.scalars(select(Job).where(Job.kind == kind)))


def test_ac_pos_10_an_invalid_signature_is_refused_and_does_nothing(square):
    client, _shop = square
    response = deliver(client, "evt-1", signature="forged")
    assert response.status_code == 400
    assert events() == [] and jobs("ingest_orders") == []


def test_an_event_is_stored_once_and_answered_without_reading_square(square, monkeypatch):
    client, (_org, pos, *_rest) = square
    first = deliver(client, "evt-1")
    assert first.json() == {"queued": "ingest_orders"}
    assert pos.asked == [], "the request reads nothing from Square (PERF-07)"
    assert [deliver(client, "evt-1").json() for _ in range(2)] == [{"duplicate": True}] * 2
    [stored] = events()
    assert (stored.status, stored.handled_by, json.loads(stored.entity_ids)) == (
        "received",
        "ingest_orders",
        ["o-1"],
    )
    assert len(jobs("ingest_orders")) == 1


def test_ac_pos_03_three_deliveries_and_a_reconciliation_consume_once(square, location):
    client, (org, pos, _connection, milk, _shot) = square
    pos.orders = [order("o-1", latte("A", 2))]
    for event_id in ("evt-1", "evt-1", "evt-1", "evt-2", "evt-3"):  # retries and later edits
        deliver(client, event_id)
    assert len(jobs("ingest_orders")) == 1, "copies wait as one job"
    _jobs()
    with SessionLocal() as db:
        assert db.scalars(select(SalesTransaction)).one().revision == 1
        assert balance(db, org, location.id, milk.id) == D(-400)
        ingest_orders(db, org)  # the scheduled read finds it again (T3.08 reconciles)
        db.commit()
    _jobs()
    with SessionLocal() as db:
        assert len(db.scalars(select(SalesTransaction)).all()) == 1
        assert balance(db, org, location.id, milk.id) == D(-400)
    assert {e.status for e in events()} == {"processed"}


def test_a_failed_read_leaves_its_events_waiting_and_the_job_tries_again(square):
    client, (_org, pos, *_rest) = square
    pos.fails = AdapterError("square is down", revoked=False)
    deliver(client, "evt-1")
    _jobs()
    assert events()[0].status == "received"
    [job] = jobs("ingest_orders")
    assert job.status == "pending" and job.attempts == 1


def test_a_catalog_burst_is_one_read_as_a_job(square, monkeypatch):
    client, (_org, _pos, connection, *_rest) = square
    reads = []

    class Catalogue:
        def list_locations(self, _credentials):
            return []

        def sync_catalog(self, _credentials, since=None):
            reads.append(1)
            product = NormalizedProduct("ITEM-TEA", "VAR-TEA", "Tea", "Regular")
            return Catalog((product,))

    monkeypatch.setattr(pos_catalog, "adapter_for", lambda _provider: Catalogue())
    monkeypatch.setattr(pos_catalog, "credentials", lambda *_args, **_kw: None)
    for event_id in ("cat-1", "cat-2", "cat-3"):
        assert deliver(client, event_id, kind="catalog.version.updated").json() == {
            "queued": "refresh_catalog"
        }
    assert len(jobs("refresh_catalog")) == 1
    _jobs()
    assert reads == [1]
    with SessionLocal() as db:
        assert (
            db.scalars(
                select(ExternalProduct).where(ExternalProduct.external_variation_id == "VAR-TEA")
            )
            .one()
            .connection_id
            == connection.id
        )
    deliver(client, "cat-4", kind="catalog.version.updated")
    _jobs()
    assert reads == [1], "a read a moment ago covers it"
    assert {e.status for e in events()} == {"processed"}


def test_an_unknown_merchant_or_type_is_kept_as_ignored_and_never_retried(square):
    client, _shop = square
    assert deliver(client, "evt-x", merchant="SOMEONE").json() == {"ignored": "unknown merchant"}
    assert deliver(client, "evt-y", kind="payment.created").json() == {"ignored": "payment.created"}
    assert deliver(client, "evt-x", merchant="SOMEONE").json() == {"duplicate": True}
    assert [e.status for e in events()] == ["ignored", "ignored"]
    assert jobs("ingest_orders") == []


def test_only_the_event_store_writes_webhook_events():
    writes = re.compile(r"WebhookEvent\(")
    offenders = [
        path.as_posix()
        for path in Path("app").rglob("*.py")
        if path.as_posix() not in {"app/webhook_events.py", "app/models.py"}
        and writes.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_a_read_marks_only_the_events_it_was_queued_for(square):
    """An event that arrives while a read runs waits for the next read."""
    client, (org, *_rest) = square
    deliver(client, "evt-early")
    deliver(client, "evt-late")
    with SessionLocal() as db:
        early, late = db.scalars(select(WebhookEvent).order_by(WebhookEvent.id)).all()
        started = datetime.now(UTC)
        late.received_at = started + timedelta(minutes=1)
        db.commit()
        assert processed_by(db, org.tenant_id, "ingest_orders", started) == 1
        db.commit()
    assert [e.status for e in events()] == ["processed", "received"]
