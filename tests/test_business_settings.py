"""T2.19: the settings module (ADM-FR-01, -03..06, AC-ADM-02, AC-ADM-06)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import role_permissions
from app.business_settings import (
    alert_email_key,
    save_alerts,
    save_defaults,
    save_profile,
    save_schedule,
    settings_for,
)
from app.consumption import resolve_sale
from app.items import NewItem, create_item
from app.models import AuditEvent, Tenant
from app.products import NewVariation, create_product, save_modifier
from app.recipes import NewLine, Owner, save_recipe
from app.sales import add_source, record_sale
from app.transform.counts import needs_review as count_needs_review
from app.transform.sales import SaleLine
from app.transform.settings import SettingsRuleError, ordered_units

D = Decimal


@pytest.fixture
def org(tenant):
    return OrgContext(tenant_id=tenant.id, permissions=role_permissions("owner"))


def test_profile_and_schedule_are_saved_and_audited(db, tenant, org):
    assert save_profile(
        db, org, name="Rosewood Coffee", address="1 High St", contact_email="Hi@Rosewood.test"
    )
    assert save_schedule(db, org, day=4, hour=16)
    db.commit()
    row = db.get(Tenant, tenant.id)
    assert (row.name, row.contact_email, row.report_day, row.report_hour) == (
        "Rosewood Coffee",
        "hi@rosewood.test",
        4,
        16,
    )
    actions = {e.action for e in db.scalars(select(AuditEvent))}
    assert {"settings.profile_changed", "settings.schedule_changed"} <= actions
    assert save_schedule(db, org, day=4, hour=16) is False  # nothing changed, nothing recorded
    with pytest.raises(SettingsRuleError, match="name"):
        save_profile(db, org, name=" ", address="", contact_email="")
    with pytest.raises(SettingsRuleError, match="email"):
        save_profile(db, org, name="R", address="", contact_email="not an email")


def test_ac_adm_02_alert_settings_take_effect_on_the_next_check(db, tenant, org):
    save_alerts(
        db,
        org,
        expiry_alert_days="5",
        digest_hour="9",
        count_review="10",
        count_review_percent="50",
        adjustment_review="100",
    )
    db.commit()
    row = settings_for(db, org)
    assert (db.get(Tenant, tenant.id).expiry_alert_days, row.digest_hour) == (5, 9)
    assert (row.count_review_cents, row.count_review_percent, row.adjustment_review_cents) == (
        1000,
        50,
        10000,
    )
    # The alert email goes out once a day, in that hour on the business's clock.
    tenant_row = db.get(Tenant, tenant.id)
    tenant_row.timezone = "America/New_York"
    db.commit()
    assert (
        alert_email_key(db, org, datetime(2026, 11, 19, 14, 30, tzinfo=UTC)) == "digest:2026-11-19"
    )
    assert alert_email_key(db, org, datetime(2026, 11, 19, 9, 30, tzinfo=UTC)) is None
    with pytest.raises(SettingsRuleError):
        save_alerts(
            db,
            org,
            expiry_alert_days="90",
            digest_hour="",
            count_review="10",
            count_review_percent="50",
            adjustment_review="100",
        )


def test_a_count_reads_the_business_review_thresholds(db, tenant, org):
    # $12 short of 100 expected: under the default $25 and 20 %, over a $10 threshold.
    assert not count_needs_review(D(-12), D(100), -1200)
    assert count_needs_review(D(-12), D(100), -1200, review_cents=1000)
    assert count_needs_review(D(-12), D(100), -1200, review_share=D("0.10"))


def test_defaults_order_units_and_make_new_products(db, tenant, org):
    save_defaults(db, org, unit_system="us", production_mode="made_ahead")
    db.commit()
    assert ordered_units(["g", "kg", "oz", "lb"], "us") == ["oz", "lb", "g", "kg"]
    soup = create_product(db, org, name="Soup", variations=[NewVariation("Bowl")])
    assert soup.production_mode == "made_ahead"
    with pytest.raises(SettingsRuleError):
        save_defaults(db, org, unit_system="imperial", production_mode="made_ahead")


def test_ac_adm_06_a_scale_factor_applies_to_sales_after_the_change_only(db, tenant, location, org):
    milk = create_item(
        db,
        org,
        NewItem(
            name="Milk",
            location_ids=[location.id],
            dimension="volume",
            display_unit="ml",
            quantity="10000",
        ),
    )
    latte = create_product(db, org, name="Latte", variations=[NewVariation("12 oz")])
    save_recipe(
        db,
        org,
        Owner("variation", latte.variations[0].id),
        [NewLine(milk.id, "200", "ml")],
        from_the_start=True,  # so a sale from minutes ago has a recipe to read
    )
    extra = save_modifier(db, org, name="Extra milk", group="", effect="scale", scale_factor="1.5")
    source = add_source(
        db, org, location_id=location.id, kind="manual", active_from=date(2026, 1, 1)
    )
    db.commit()

    def sale(ref, when):
        return record_sale(
            db,
            org,
            source,
            source_reference=ref,
            occurred_at=when,
            lines=[SaleLine("1", D(1), latte.variations[0].id, modifiers=((extra.id, 1),))],
        )

    before = sale("before", datetime.now(UTC) - timedelta(minutes=5))
    save_modifier(
        db,
        org,
        name="Extra milk",
        group="",
        effect="scale",
        scale_factor="2",
        modifier_id=extra.id,
    )
    db.commit()
    after = sale("after", datetime.now(UTC) + timedelta(seconds=1))
    db.commit()
    [early] = resolve_sale(db, org, before).values()
    [late] = resolve_sale(db, org, after).values()
    assert early.uses[milk.id] == D(300)  # still 1.5: worked out again, it reads its own moment
    assert late.uses[milk.id] == D(400)


def test_the_settings_pages_save_through_the_browser(db, tenant):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        client.post("/login", data={"email": "owner@rosewood.test", "password": "a-good-password"})
        assert client.get("/app/settings/business").status_code == 200
        saved = client.post("/app/settings/business/schedule", data={"day": "2", "hour": "18"})
        assert "Saved." in saved.text
        page = client.get("/app/settings/alerts")
        assert page.status_code == 200 and "owner@rosewood.test" in page.text
        refused = client.post(
            "/app/settings/alerts",
            data={
                "expiry_alert_days": "2",
                "digest_hour": "",
                "count_review": "abc",
                "count_review_percent": "20",
                "adjustment_review": "50",
            },
        )
        assert "is an amount" in refused.text
