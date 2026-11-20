"""T2.22: Standalone works with no POS configured and no POS credentials (AC-SAL-01, AC-SAL-07,
MNT-08, DR-29).

A business signs up and runs every R1 workflow through the pages, as a browser would, while any
attempt to reach Square fails the test; and a static check keeps provider code inside the adapter.
"""

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.config import get_settings
from app.db import SessionLocal
from app.ledger.posting import balance
from app.models import (
    InventoryItem,
    Location,
    ProductVariation,
    ProviderConnection,
    Tenant,
    WasteReason,
)
from app.orchestration.stripe_events import handle_event
from app.transform.business_days import business_date

D = Decimal
APP = Path("app")
# The seams where the product meets a POS: syncing, connecting, and receiving its webhooks.
PROVIDER_SEAMS = {"app/orchestration/sync.py", "app/web/dashboard.py", "app/web/webhooks.py"}


# --- AC-SAL-07: provider code stays in the adapter -----------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


# Provider-independent: the adapter contract and the registry may be used anywhere (T3.01).
NEUTRAL = {"app.providers", "app.providers.base"}


def test_ac_sal_07_provider_code_lives_only_in_the_adapter():
    users = {
        path.as_posix()
        for path in APP.rglob("*.py")
        if not path.as_posix().startswith("app/providers/")
        and any(m.startswith("app.providers.") and m not in NEUTRAL for m in _imports(path))
    }
    assert users <= PROVIDER_SEAMS, users - PROVIDER_SEAMS
    base = _imports(APP / "providers" / "base.py")
    assert not any(m.startswith("app.providers.") for m in base), "the contract names no provider"
    named = [
        path.as_posix()
        for path in APP.rglob("*square*.py")
        if not path.as_posix().startswith("app/providers/")
    ]
    assert named == []


def test_the_business_rules_never_reach_the_pos_seams():
    """The ledger, the rules and scoped access, and the domain services, work with no POS."""
    seams = {"app.orchestration.sync", "app.web.webhooks", "app.web.dashboard"}
    core = [
        *APP.joinpath("transform").rglob("*.py"),
        *APP.joinpath("ledger").rglob("*.py"),
        *APP.joinpath("access").rglob("*.py"),
        *(p for p in APP.glob("*.py") if p.name not in {"main.py", "cli.py"}),
    ]
    reaching = {p.as_posix() for p in core if _imports(p) & seams}
    assert reaching == set(), reaching


# --- AC-SAL-01: every R1 workflow, end to end, with no POS ---------------------------------------


@pytest.fixture
def no_pos(monkeypatch):
    """Every POS setting unset, and any attempt to reach Square fails the test."""
    settings = get_settings()
    for name in ("square_app_id", "square_app_secret", "square_webhook_signature_key"):
        monkeypatch.setattr(settings, name, "", raising=False)

    def refuse(*_args, **_kwargs):
        raise AssertionError("a Standalone workflow tried to reach Square")

    monkeypatch.setattr("app.providers.square.client.SquareClient.__init__", refuse)


def _id(response) -> int:
    return int(response.url.path.rstrip("/").rsplit("/", 1)[-1])


def test_ac_sal_01_every_r1_workflow_with_no_pos(db, no_pos):
    client = TestClient(app_instance())
    client.__enter__()
    signed_up = client.post(
        "/signup",
        data={
            "business_name": "Corner Juice",
            "email": "owner@corner.test",
            "password": "a-long-enough-password",
        },
    )
    assert signed_up.url.path == "/app"
    tenant = db.scalars(select(Tenant).where(Tenant.name == "Corner Juice")).one()

    # Onboarding: no POS, a location, items from a file.
    client.post("/app/onboarding/payments", data={"answer": "none"})
    placed = client.post(
        "/app/locations/new",
        data={
            "name": "Shop",
            "timezone": "UTC",
            "currency": "USD",
            "business_day_cutoff_hour": "0",
            "open_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        },
    )
    assert "saved=1" in str(placed.url)
    shop = db.scalars(select(Location).where(Location.tenant_id == tenant.id)).one()
    items_csv = (
        b"Name,Unit,Quantity,Reorder point,Cost,Type\r\n"
        b"Milk,l,0,5,1.20,raw\r\n"
        b"Ginger,kg,0,1,8,raw\r\n"
        b"Cups,each,0,50,0.10,packaging\r\n"
        b"Ginger juice,l,0,0,0,prepared\r\n"
    )
    checked = client.post(
        "/app/items/import",
        data={"location_id": shop.id},
        files={"file": ("items.csv", items_csv, "text/csv")},
    )
    assert "4 rows ready" in checked.text
    client.post(checked.url.path, data={"location_id": shop.id})
    item = {
        i.name: i
        for i in db.scalars(select(InventoryItem).where(InventoryItem.tenant_id == tenant.id))
    }
    assert set(item) == {"Milk", "Ginger", "Cups", "Ginger juice"}

    # Purchasing: a supplier and a delivery received against it.
    supplier = client.post(
        "/app/suppliers", data={"name": "Farm Co", "lead_time_days": "2", "delivery_days": ["mon"]}
    )
    supplier_id = _id(supplier)
    delivery = client.post(
        "/app/deliveries", data={"location_id": shop.id, "supplier_id": supplier_id}
    )
    receiving_id = _id(delivery)
    for name, accepted, unit, price in (
        ("Milk", "20", "l", "1.20"),
        ("Ginger", "5", "kg", "8"),
        ("Cups", "200", "each", "0.10"),
    ):
        client.post(
            f"/app/deliveries/{receiving_id}/lines",
            data={
                "inventory_item_id": item[name].id,
                "accepted": accepted,
                "unit": unit,
                "unit_price": price,
            },
        )
    posted = client.post(f"/app/deliveries/{receiving_id}/post")
    assert posted.status_code == 200

    # Production: a recipe for the juice, and four litres made from it.
    client.post(
        f"/app/recipes/item/{item['Ginger juice'].id}",
        data={
            "item": [item["Ginger"].id],
            "quantity": ["0.5"],
            "unit": ["kg"],
            "yield_quantity": "1",
            "yield_unit": "l",
        },
    )
    made = client.post(
        "/app/production",
        data={
            "location_id": shop.id,
            "inventory_item_id": item["Ginger juice"].id,
            "times": "4",
            "actual_yield": "4",
        },
    )
    assert made.status_code == 200

    # A product with a recipe, and a day's sales entered by hand. The juice is made ahead, so a
    # sale takes the juice the batch made, not the ginger (DR-18); set once for new products.
    client.post(
        "/app/settings/business/defaults",
        data={"unit_system": "metric", "production_mode": "made_ahead"},
    )
    product = client.post(
        "/app/products/new",
        data={"name": "Juice shot", "variation_name": ["Small"], "variation_price": ["4.00"]},
    )
    assert product.status_code == 200
    size = db.scalars(select(ProductVariation).where(ProductVariation.tenant_id == tenant.id)).one()
    client.post(
        f"/app/recipes/variation/{size.id}",
        data={
            "item": [item["Ginger juice"].id, item["Cups"].id],
            "quantity": ["0.1", "1"],
            "unit": ["l", ""],
        },
    )
    today = business_date(datetime.now(UTC), shop.timezone, shop.business_day_cutoff_hour)
    sold = client.post(
        "/app/sales/daily",
        data={"location_id": shop.id, "day": today.isoformat(), f"qty_{size.id}": "10"},
    )
    assert sold.status_code == 200

    # The floor: waste, a staff meal, and a count.
    client.get("/app/waste")  # the default reasons are written on first visit
    reason = db.scalars(select(WasteReason).where(WasteReason.tenant_id == tenant.id)).first()
    client.post(
        "/app/waste",
        data={
            "location_id": shop.id,
            "reason_id": reason.id,
            "inventory_item_id": item["Milk"].id,
            "quantity": "2",
            "unit": "l",
        },
    )
    usage_page = client.get("/app/usage").text
    token = usage_page.split('name="token" value="', 1)[1].split('"', 1)[0]
    client.post(
        "/app/usage",
        data={
            "location_id": shop.id,
            "token": token,
            "reason": "staff_meal",
            "item_1": item["Milk"].id,
            "qty_1": "1",
            "unit_1": "l",
        },
    )
    counted = client.post(
        "/app/onboarding/count",
        data={"location_id": shop.id, f"qty_{item['Milk'].id}": "16"},
    )
    assert "Counted 1 item" in counted.text

    # Reports, reorder, valuation and the weekly summary all answer.
    for page in ("/app/reports", "/app/reorder", "/app/valuation", "/app/report", "/app/floor"):
        assert client.get(page).status_code == 200, page
    sheet = client.get("/app/reports.csv", params={"table": "consumption", "range": "7d"})
    assert sheet.status_code == 200 and "Ginger juice" in sheet.text

    # Billing: a paid subscription arrives with no POS anywhere.
    handle_event(
        db,
        {
            "id": "evt_standalone",
            "type": "checkout.session.completed",
            "created": int(datetime.now(UTC).timestamp()),
            "data": {
                "object": {
                    "id": "cs_1",
                    "customer": "cus_1",
                    "subscription": "sub_1",
                    "client_reference_id": str(tenant.id),
                    "metadata": {"tenant_id": str(tenant.id), "plan": "starter"},
                }
            },
        },
    )
    db.commit()

    # What stock the ledger holds is what every workflow did to it.
    with SessionLocal() as fresh:
        org = OrgContext.system(tenant.id)

        def held(name: str) -> Decimal:
            return balance(fresh, org, shop.id, item[name].id)

        assert held("Milk") == D(16000)  # 20 l received, 2 wasted, 1 used, counted at 16
        assert held("Ginger") == D(3000)  # 5 kg received, 4 x 0.5 kg into juice
        assert held("Ginger juice") == D(3000)  # 4 l made, 10 shots of 0.1 l sold
        assert held("Cups") == D(190)  # 200 received, 10 sold
        assert fresh.scalars(select(ProviderConnection)).first() is None
        assert fresh.get(Tenant, tenant.id).stripe_subscription_id == "sub_1"


def app_instance():
    from app.main import app

    return app
