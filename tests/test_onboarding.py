"""T2.20-b: step 0, skipping steps and the opening count (SAAS §5, WF-01)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.items import NewItem, create_item
from app.models import AuditEvent, InventoryMovement, StockItem
from app.onboarding import answer_payments, checklist, onboarding_for, opening_count, set_skipped
from app.transform.onboarding import OnboardingRuleError, check_answer

D = Decimal
NOW = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


@pytest.fixture
def org(tenant):
    return OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))


def _step(db, org, key):
    return next(s for s in checklist(db, org) if s.key == key)


def test_step_0_routes_and_keeps_what_was_said(db, tenant, org):
    assert check_answer("pos_supported", "") == ("pos_supported", "Square")
    assert check_answer("none_wants_pos", "anything") == ("none_wants_pos", None)
    with pytest.raises(OnboardingRuleError, match="which POS"):
        check_answer("pos_unsupported", " ")

    answer_payments(db, org, answer="pos_unsupported", pos_name="Lightspeed  Restaurant")
    db.commit()
    row = onboarding_for(db, org)
    assert (row.payment_answer, row.pos_name) == ("pos_unsupported", "Lightspeed Restaurant")
    assert _step(db, org, "sales").link == "/app/sales/import"  # file import until supported
    assert db.scalars(
        select(AuditEvent).where(AuditEvent.action == "settings.onboarding_answered")
    ).one()

    answer_payments(db, org, answer="pos_supported")
    db.commit()
    assert _step(db, org, "sales").link == "/connect/square"


def test_a_step_can_be_skipped_and_taken_back_up(db, tenant, org):
    set_skipped(db, org, "suppliers", True)
    db.commit()
    assert _step(db, org, "suppliers").skipped
    set_skipped(db, org, "suppliers", False)
    db.commit()
    assert not _step(db, org, "suppliers").skipped
    with pytest.raises(OnboardingRuleError):
        set_skipped(db, org, "nonsense", True)


def test_the_opening_count_opens_new_items_and_corrects_the_rest(db, tenant, location, org):
    def item(name, quantity):
        return create_item(
            db,
            org,
            NewItem(
                name=name,
                location_ids=[location.id],
                dimension="count",
                display_unit="each",
                quantity=quantity,
            ),
        )

    fresh, known, untouched = item("Lids", "0"), item("Cups", "5"), item("Straws", "0")
    db.commit()
    done = opening_count(
        db,
        org,
        location_id=location.id,
        entries={fresh.id: ("40", ""), known.id: ("8", ""), untouched.id: ("", "")},
        now=NOW,
    )
    db.commit()
    assert {(c.item_id, c.opened, c.difference) for c in done} == {
        (fresh.id, True, D(0)),
        (known.id, False, D(3)),
    }
    kinds = {
        (m.inventory_item_id, m.movement_type)
        for m in db.scalars(select(InventoryMovement).where(InventoryMovement.occurred_at == NOW))
    }
    assert kinds == {(fresh.id, "opening_balance"), (known.id, "count_variance")}
    stock = {s.inventory_item_id: s for s in db.scalars(select(StockItem))}
    assert (stock[fresh.id].qty_base, stock[known.id].qty_base) == (D(40), D(8))
    assert stock[fresh.id].last_counted_at == NOW
    assert stock[untouched.id].last_counted_at != NOW  # left blank, so not counted
    assert _step(db, org, "count").done
    with pytest.raises(OnboardingRuleError, match="Cups"):
        opening_count(db, org, location_id=location.id, entries={known.id: ("-1", "")})


def test_onboarding_through_the_dashboard(db, tenant, location, org):
    from fastapi.testclient import TestClient

    from app.main import app

    create_item(
        db,
        org,
        NewItem(name="Lids", location_ids=[location.id], dimension="count", display_unit="each"),
    )
    db.commit()
    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        assert "How do you take payments today?" in client.get("/app").text
        answered = client.post("/app/onboarding/payments", data={"answer": "none"})
        assert "enter each day" in answered.text
        assert "How do you take payments today?" not in answered.text
        skipped = client.post("/app/onboarding/skip", data={"step": "team", "skip": "yes"})
        assert "Take it back up" in skipped.text
        page = client.get("/app/onboarding/count")
        assert "Lids" in page.text
        item_id = page.text.split('name="qty_', 1)[1].split('"', 1)[0]
        counted = client.post(
            "/app/onboarding/count", data={"location_id": location.id, f"qty_{item_id}": "12"}
        )
        assert "Counted 1 item" in counted.text
