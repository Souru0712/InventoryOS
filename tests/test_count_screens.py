"""T1.23-b: count session screens (REC-FR-01, -02, -09, WF-09, AC-REC-01, AC-REC-05)."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import (
    ItemLocation,
    ItemPackage,
    Membership,
    StockCount,
    StockCountLine,
    StockItem,
    TrackMode,
    User,
)
from app.security import hash_password
from app.units import Dimension
from tests.ledger_helpers import opened

D = Decimal
PASSWORD = "a-good-password"  # noqa: S105 (the test accounts', as in conftest)


def stock(db, location, name, qty, area=None, cost="250"):
    item = StockItem(
        tenant_id=location.tenant_id,
        location_id=location.id,
        slug=name.lower().replace(" ", "-"),
        name=name,
        dimension=Dimension.COUNT,
        stock_unit="each",
        track_mode=TrackMode.MANUAL,
        qty_base=D(qty),
        cost_per_base_cents=D(cost),
    )
    db.add(item)
    opened(db, item)
    if area:
        db.get(ItemLocation, item.item_location_id).storage_area = area
    db.commit()
    return item


def signed_in(db, tenant, role=None) -> TestClient:
    email = "owner@rosewood.test"
    if role is not None:
        email = f"{role}@rosewood.test"
        user = User(tenant_id=tenant.id, email=email, password_hash=hash_password(PASSWORD))
        db.add(user)
        db.flush()
        db.add(Membership(tenant_id=tenant.id, user_id=user.id, role=role))
        db.commit()
    client = TestClient(app)
    client.__enter__()
    client.post("/login", data={"email": email, "password": PASSWORD})
    return client


@pytest.fixture
def bar(db, tenant, location):
    """Two areas: lemons and ale behind the bar (lemons come by the case of 12), milk in the
    walk-in."""
    lemons = stock(db, location, "Lemons", "40", "Bar")
    ale = stock(db, location, "Ale", "10", "Bar")
    milk = stock(db, location, "Milk", "8", "Walk-in")
    db.add(
        ItemPackage(
            tenant_id=tenant.id,
            inventory_item_id=lemons.inventory_item_id,
            name="case",
            quantity=D(12),
            unit="each",
        )
    )
    db.commit()
    return {"lemons": lemons, "ale": ale, "milk": milk}


def started(client, location, **form) -> str:
    response = client.post(
        "/app/counts",
        data={"location_id": location.id, "scope": "full", **form},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return response.headers["location"]


def test_ac_rec_01_an_area_count_lists_in_storage_order_and_takes_cases_plus_each(
    db, tenant, location, bar
):
    owner = signed_in(db, tenant)
    url = started(owner, location, scope="area", area="Bar")
    page = owner.get(url).text
    assert page.index("Ale") < page.index("Lemons")
    assert "Milk" not in page  # the walk-in is not part of this count
    assert '<option value="case">case</option>' in page  # lemons come by the case

    lemons = bar["lemons"].inventory_item_id
    after = owner.post(
        f"{url}/lines",
        data={
            "item_id": lemons,
            "pack_quantity": "3",
            "pack": "case",
            "quantity": "4",
            "unit": "each",
        },
        follow_redirects=False,
    )
    assert after.headers["location"] == f"{url}#post"  # lemons were the last item at the bar
    line = db.scalars(select(StockCountLine)).one()
    assert (line.counted_qty_base, line.entered) == (D(40), "3 case + 4 each")
    assert "counted 40 each" in owner.get(url).text


def test_counting_posting_and_reading_it_back(db, tenant, location, bar):
    """AC-REC-05: the history shows each session with totals, and its line detail."""
    owner = signed_in(db, tenant)
    url = started(owner, location)
    ale, milk = bar["ale"].inventory_item_id, bar["milk"].inventory_item_id
    first = owner.post(
        f"{url}/lines",
        data={"item_id": ale, "quantity": "10", "unit": "each", "next_item": str(milk)},
        follow_redirects=False,
    )
    assert first.headers["location"] == f"{url}#item-{milk}"  # on to the next item
    owner.post(f"{url}/lines", data={"item_id": milk, "quantity": "9", "unit": "each"})
    owner.post(
        f"{url}/lines",
        data={"item_id": milk, "quantity": "2", "unit": "each", "reason": "unrecorded_waste"},
    )  # counted again

    posted = owner.post(f"{url}/post").text
    assert "posted: 2 counted, 1 adjusted, 1 sent for review." in posted
    # Line detail: milk 2 counted against 8 expected, -6, -$15.00 at $2.50 each.
    for shown in ("Milk", "2 each", "8 each", "-6", "-$15.00", "Unrecorded waste", "review"):
        assert shown in posted
    history = owner.get("/app/counts").text
    assert f'href="{url}"' in history and "posted" in history and "-$15.00" in history
    assert db.scalars(select(StockCount)).one().status == "posted"
    again = owner.post(f"{url}/lines", data={"item_id": ale, "quantity": "1", "unit": "each"})
    assert "This count has been posted; start a new one." in again.text


def test_mistakes_are_explained_and_nothing_is_recorded(db, tenant, location, bar):
    owner = signed_in(db, tenant)
    url = started(owner, location)
    ale = bar["ale"].inventory_item_id
    refused = owner.post(f"{url}/lines", data={"item_id": ale, "quantity": "-2", "unit": "each"})
    assert "A count cannot be below zero." in refused.text
    empty = owner.post(f"{url}/post")
    assert "Count at least one item before posting." in empty.text
    assert db.scalars(select(StockCountLine)).all() == []
    assert db.scalars(select(StockCount)).one().status == "open"


def test_an_employee_counts_without_seeing_money_and_read_only_only_reads(
    db, tenant, location, bar
):
    employee = signed_in(db, tenant, "employee")
    url = started(employee, location)
    employee.post(
        f"{url}/lines",
        data={"item_id": bar["milk"].inventory_item_id, "quantity": "2", "unit": "each"},
    )
    page = employee.post(f"{url}/post").text
    assert "-6" in page and "$" not in page.split("<main")[-1]  # no amounts anywhere
    assert "$" not in employee.get("/app/counts").text.split("<main")[-1]

    reader = signed_in(db, tenant, "read_only")
    assert reader.get(url).status_code == 200
    listing = reader.get("/app/counts").text
    assert "Start counting" not in listing
    assert reader.post("/app/counts", data={"location_id": location.id}).status_code == 403
