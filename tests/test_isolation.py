"""T1.03 (AC-SAAS-01, SAAS-T-09): a member of one business never reaches another's data.

The cases are generated from the app's route table. Every route that takes an identifier is
called by a signed-in member of business A with business B's identifiers: it must answer 404 or
403, show nothing of B, and leave every row B owns exactly as it was. The same call with A's own
identifiers must succeed, so a route that refuses everything cannot pass by accident. A new
route that takes an identifier fails the coverage test until it has a case here.
"""

import hashlib
import typing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.access import OrgContext
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (
    Document,
    DocumentLine,
    DocumentStatus,
    ExceptionRecord,
    ExternalLocation,
    InventoryCategory,
    Invitation,
    ItemPackage,
    ItemUnitConversion,
    Location,
    Membership,
    Modifier,
    Product,
    ProductIdentifier,
    ProductionOrder,
    ProductVariation,
    Promotion,
    PromotionStatus,
    ProviderConnection,
    Receiving,
    ReceivingLine,
    SalesImport,
    StockCount,
    StockItem,
    Supplier,
    SupplierItem,
    Tenant,
    TrackMode,
    User,
    WasteEvent,
    WasteReason,
)
from app.recipes import NewLine, Owner, save_recipe
from app.security import hash_password
from app.storage import get_storage
from app.units import Dimension
from tests.accounts import confirm_email
from tests.batch_helpers import dated
from tests.sample_files import jpeg
from tests.test_async_correctness import api_routes

# Integer parameters that are quantities, not identifiers. Any other integer is an identifier.
NOT_IDENTIFIERS = {"lead_time_days", "business_day_cutoff_hour", "day", "hour"}  # numbers, not rows


def _is_int(annotation) -> bool:
    return annotation is int or int in typing.get_args(annotation)


def identifier_params(route) -> list[str]:
    """Every path parameter, and every query or form field that names a row."""
    found = []

    def walk(dependant):
        for param in dependant.path_params:
            found.append(param.name)
        for param in dependant.query_params + dependant.body_params:
            # location_filter reads ?location= as text so a form's "All" (empty) is accepted.
            names_a_row = param.name in ("id", "location") or param.name.endswith("_id")
            if names_a_row or (
                _is_int(param.field_info.annotation) and param.name not in NOT_IDENTIFIERS
            ):
                found.append(param.name)
        for sub in dependant.dependencies:
            walk(sub)

    walk(route.dependant)
    return sorted(set(found))


def routes_with_identifiers() -> dict[tuple[str, str], list[str]]:
    result = {}
    for route in api_routes(app.routes):
        params = identifier_params(route)
        if params:
            for method in route.methods:
                result[(method, route.path)] = params
    return result


# --- the cases: how to call each route for one business's rows ------------------------------------
# ids holds that business's rows: location, item, promo_item, batch, promotion, document, line.


def _apply_form(ids):
    line = ids["line"]
    return {
        "data": {
            f"line_{line}_action": "apply",
            f"line_{line}_item": ids["item"],
            f"line_{line}_qty": "2",
        }
    }


SALES_CSV = b"date,item,qty\r\n2026-11-02,Bars,3\r\n"
ITEMS_CSV = b"Name,Unit,Quantity\r\nFresh basil,g,100\r\n"

CASES = {
    ("GET", "/app"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/expiring"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/health"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/reorder"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/reorder.csv"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/promotions"): lambda ids: {"params": {"location": ids["location"]}},
    ("POST", "/app/expiring/label"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "quantity": "2", "expires_on": "2030-01-31"}
    },
    ("POST", "/app/expiring/perishable"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "perishable": "yes"}
    },
    ("POST", "/app/expiring/{batch_id}/remove"): lambda ids: {},
    ("POST", "/app/expiring/{batch_id}/dispose"): lambda ids: {},
    ("GET", "/app/production"): lambda ids: {
        "params": {"item": ids["inventory_item"], "location": ids["location"]}
    },
    ("POST", "/app/production"): lambda ids: {
        "data": {"location_id": ids["location"], "inventory_item_id": ids["inventory_item"]}
    },
    ("POST", "/app/production/{order_id}/void"): lambda ids: {},
    ("GET", "/app/suppliers/{supplier_id}"): lambda ids: {},
    ("POST", "/app/suppliers/{supplier_id}"): lambda ids: {"data": {"name": "Renamed"}},
    ("POST", "/app/suppliers/{supplier_id}/active"): lambda ids: {"data": {"active": "yes"}},
    ("POST", "/app/suppliers/{supplier_id}/items"): lambda ids: {
        "data": {"inventory_item_id": ids["inventory_item"], "pack_quantity": "2"}
    },
    ("GET", "/app/sales/daily"): lambda ids: {"params": {"location": ids["location"]}},
    ("POST", "/app/sales/import"): lambda ids: {
        "data": {"location_id": ids["location"]},
        "files": {"file": ("sales.csv", SALES_CSV, "text/csv")},
    },
    ("GET", "/app/sales/import/{import_id}"): lambda ids: {},
    ("POST", "/app/sales/import/{import_id}/mapping"): lambda ids: {
        "data": {"date_column": "date", "product_column": "item", "quantity_column": "qty"}
    },
    ("POST", "/app/sales/import/{import_id}/match"): lambda ids: {
        "data": {"name": "Bars", "product_variation_id": ids["variation"]}
    },
    ("POST", "/app/sales/import/{import_id}/post"): lambda ids: {},
    ("POST", "/app/sales/daily"): lambda ids: {
        "data": {"location_id": ids["location"], "day": "2026-11-02"}
    },
    ("GET", "/app/usage"): lambda ids: {"params": {"location": ids["location"]}},
    ("GET", "/app/reports"): lambda ids: {"params": {"location": ids["location"]}},
    ("POST", "/app/items/import"): lambda ids: {
        "data": {"location_id": ids["location"]},
        "files": {"file": ("items.csv", ITEMS_CSV, "text/csv")},
    },
    ("GET", "/app/items/import/{digest}"): lambda ids: {"params": {"location": ids["location"]}},
    ("POST", "/app/items/import/{digest}"): lambda ids: {"data": {"location_id": ids["location"]}},
    ("POST", "/app/floor/person"): lambda ids: {"data": {"membership_id": ids["membership"]}},
    ("GET", "/app/onboarding/count"): lambda ids: {"params": {"location": ids["location"]}},
    ("POST", "/app/onboarding/count"): lambda ids: {
        "data": {"location_id": ids["location"], f"qty_{ids['inventory_item']}": "3"}
    },
    ("POST", "/app/items/quick-add"): lambda ids: {
        "data": {"location_id": ids["location"], "name_1": "Fresh mint"}
    },
    ("GET", "/app/reports.csv"): lambda ids: {
        "params": {"location": ids["location"], "table": "consumption"}
    },
    ("POST", "/app/usage"): lambda ids: {
        "data": {
            "location_id": ids["location"],
            "token": "a" * 32,
            "reason": "staff_meal",
            "item_1": ids["inventory_item"],
            "qty_1": "1",
        }
    },
    ("POST", "/app/deliveries"): lambda ids: {
        "data": {"location_id": ids["location"], "supplier_id": ids["supplier"]}
    },
    ("GET", "/app/deliveries/{receiving_id}"): lambda ids: {},
    ("POST", "/app/deliveries/{receiving_id}/lines"): lambda ids: {
        "data": {
            "inventory_item_id": ids["inventory_item"],
            "supplier_item_id": ids["supplier_item"],
            "accepted": "1",
        }
    },
    ("POST", "/app/deliveries/{receiving_id}/post"): lambda ids: {},
    ("POST", "/app/deliveries/lines/{line_id}/remove"): lambda ids: {},
    ("POST", "/app/deliveries/lines/{line_id}/correct"): lambda ids: {
        "data": {"accepted": "1", "reason": "recount"}
    },
    ("POST", "/app/suppliers/items/{supplier_item_id}"): lambda ids: {
        "data": {"pack_quantity": "3"}
    },
    ("GET", "/app/expiring/batches/{batch_id}"): lambda ids: {},
    ("POST", "/app/expiring/batches/{batch_id}/count"): lambda ids: {"data": {"quantity": "1"}},
    ("GET", "/app/waste"): lambda ids: {
        "params": {"item": ids["inventory_item"], "location": ids["location"]}
    },
    ("POST", "/app/waste"): lambda ids: {
        "data": {
            "location_id": ids["location"],
            "reason_id": ids["waste_reason"],
            "inventory_item_id": ids["inventory_item"],
            "quantity": "1",
        }
    },
    ("POST", "/app/waste/{event_id}/void"): lambda ids: {},
    ("GET", "/app/waste/log"): lambda ids: {
        "params": {
            "location": ids["location"],
            "reason": ids["waste_reason"],
            "item": ids["inventory_item"],
        }
    },
    ("POST", "/app/waste/reasons/{reason_id}"): lambda ids: {"data": {"label": "Dropped"}},
    ("GET", "/app/catalog"): lambda ids: {"params": {"category": ids["category"]}},
    ("POST", "/app/catalog/new"): lambda ids: {
        "data": {"name": "Oat milk", "location_ids": [ids["location"]], "display_unit": "each"}
    },
    ("GET", "/app/catalog/{inventory_item_id}"): lambda ids: {},
    ("POST", "/app/catalog/{inventory_item_id}"): lambda ids: {
        "data": {"name": "Renamed bars", "item_type": "resale", "display_unit": "each"}
    },
    ("POST", "/app/catalog/{inventory_item_id}/settings/{item_location_id}"): lambda ids: {
        "data": {"reorder_point": "2"}
    },
    ("POST", "/app/catalog/{inventory_item_id}/deactivate"): lambda ids: {},
    ("POST", "/app/catalog/{inventory_item_id}/activate"): lambda ids: {},
    ("POST", "/app/catalog/{inventory_item_id}/stock-at"): lambda ids: {
        "data": {"location_id": ids["location"]}
    },
    ("POST", "/app/catalog/categories/{category_id}/rename"): lambda ids: {
        "data": {"name": "Renamed"}
    },
    ("POST", "/app/catalog/categories/{category_id}/merge"): lambda ids: {
        "data": {"into_id": ids["category2"]}
    },
    ("POST", "/app/catalog/{inventory_item_id}/packages"): lambda ids: {
        "data": {"name": "crate", "quantity": "6", "unit": "each"}
    },
    ("POST", "/app/catalog/{inventory_item_id}/packages/{package_id}/purchase"): lambda ids: {},
    ("POST", "/app/catalog/{inventory_item_id}/packages/{package_id}/remove"): lambda ids: {},
    ("POST", "/app/catalog/{inventory_item_id}/conversions"): lambda ids: {
        "data": {"from_quantity": "1", "from_unit": "each", "to_quantity": "330", "to_unit": "ml"}
    },
    ("POST", "/app/catalog/{inventory_item_id}/conversions/{conversion_id}/remove"): lambda ids: {},
    ("POST", "/app/catalog/categories/{category_id}/archive"): lambda ids: {},
    ("POST", "/app/catalog/categories/{category_id}/restore"): lambda ids: {},
    ("POST", "/app/items/{item_id}/count"): lambda ids: {"data": {"quantity": "3"}},
    ("POST", "/app/items/{item_id}/thresholds"): lambda ids: {"data": {"reorder_point": "1"}},
    ("POST", "/app/receiving"): lambda ids: {
        "data": {"location_id": ids["location"]},
        "files": {"photo": ("delivery.jpg", jpeg("isolation"), "image/jpeg")},
    },
    ("GET", "/app/receiving/{document_id}"): lambda ids: {},
    ("GET", "/app/products/{product_id}"): lambda ids: {},
    ("GET", "/app/catalog/{inventory_item_id}/recipes"): lambda ids: {},
    ("GET", "/app/recipes/{kind}/{owner_id}"): lambda ids: {},
    ("GET", "/app/recipes/{kind}/{owner_id}/edit"): lambda ids: {},
    ("POST", "/app/recipes/{kind}/{owner_id}"): lambda ids: {
        "data": {"item": ids["inventory_item"], "quantity": "1", "unit": ""}
    },
    ("POST", "/app/products/{product_id}"): lambda ids: {
        "data": {"name": "House juice", "category": "Juices"}
    },
    ("POST", "/app/products/{product_id}/active"): lambda ids: {"data": {"active": "no"}},
    ("POST", "/app/products/{product_id}/variations"): lambda ids: {"data": {"name": "Large"}},
    ("POST", "/app/products/{product_id}/variations/{variation_id}"): lambda ids: {
        "data": {"name": "Small", "price": "4.00", "active": "yes"}
    },
    ("POST", "/app/modifiers/{modifier_id}"): lambda ids: {
        "data": {"name": "Extra ice", "effect": "scale", "scale_factor": "0.9", "active": "yes"}
    },
    ("GET", "/app/receiving/{document_id}/image"): lambda ids: {},
    ("POST", "/app/receiving/{document_id}/reparse"): lambda ids: {},
    ("POST", "/app/receiving/{document_id}/apply"): _apply_form,
    ("POST", "/app/promotions/accept"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "price": ""}
    },
    ("POST", "/app/promotions/dismiss"): lambda ids: {"data": {"stock_item_id": ids["item"]}},
    ("POST", "/app/promotions/snooze"): lambda ids: {"data": {"stock_item_id": ids["item"]}},
    ("POST", "/app/promotions/{promotion_id}/end"): lambda ids: {},
    ("POST", "/app/scan/link"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "code": "4006381333931"}
    },
    ("POST", "/app/scan/receive"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "quantity": "1"}
    },
    ("POST", "/app/scan/count"): lambda ids: {
        "data": {"stock_item_id": ids["item"], "quantity": "5"}
    },
    ("GET", "/app/scan/codes/{stock_item_id}"): lambda ids: {},
    ("GET", "/app/locations/{location_id}"): lambda ids: {},
    ("POST", "/app/locations/{location_id}"): lambda ids: {
        "data": {
            "name": "Renamed",
            "timezone": "America/Chicago",
            "currency": "USD",
            "open_days": ["mon"],
        }
    },
    ("POST", "/app/locations/{location_id}/archive"): lambda ids: {},
    ("POST", "/app/connection/locations/{external_location_id}"): lambda ids: {
        "data": {"location_id": ids["location"]}
    },
    ("POST", "/app/organizations/switch"): lambda ids: {
        "data": {"membership_id": ids["membership"]}
    },
    ("POST", "/app/locations/{location_id}/restore"): lambda ids: {},
    ("POST", "/app/team/invite"): lambda ids: {
        "data": {
            "email": "new.hire@example.test",
            "role": "employee",
            "scope": "some",
            "location_ids": [ids["location"]],
        }
    },
    ("POST", "/app/team/{membership_id}/role"): lambda ids: {
        "data": {"role": "manager", "scope": "some", "location_ids": [ids["location"]]}
    },
    ("POST", "/app/team/{membership_id}/remove"): lambda ids: {},
    ("POST", "/app/team/invitations/{invitation_id}/withdraw"): lambda ids: {},
    ("GET", "/app/audit"): lambda ids: {"params": {"person": ids["user"]}},
    ("POST", "/app/counts"): lambda ids: {
        "data": {"location_id": ids["location"], "scope": "full"}
    },
    ("GET", "/app/counts/{count_id}"): lambda ids: {},
    ("POST", "/app/counts/{count_id}/lines"): lambda ids: {
        "data": {"item_id": ids["inventory_item"], "quantity": "1", "unit": "each"}
    },
    ("POST", "/app/counts/{count_id}/post"): lambda ids: {},
    ("GET", "/app/valuation"): lambda ids: {"params": {"location": ids["location"]}},
    ("POST", "/app/catalog/{inventory_item_id}/cost"): lambda ids: {
        "data": {"location_id": ids["location"], "price": "1.00", "unit": "each"}
    },
    ("GET", "/app/catalog/{inventory_item_id}/history"): lambda ids: {},
    ("GET", "/app/catalog/{inventory_item_id}/adjust"): lambda ids: {},
    ("POST", "/app/catalog/{inventory_item_id}/adjust"): lambda ids: {
        "data": {
            "location_id": ids["location"],
            "direction": "add",
            "quantity": "1",
            "unit": "each",
            "reason": "found",
        }
    },
    ("POST", "/app/exceptions/{exception_id}/acknowledge"): lambda ids: {},
    ("POST", "/app/exceptions/{exception_id}/dismiss"): lambda ids: {
        "data": {"reason": "Counted it myself"}
    },
}

PATH_ROWS = {
    "location_id": "location",
    "batch_id": "batch",
    "item_id": "item",
    "stock_item_id": "item",
    "document_id": "document",
    "promotion_id": "promotion",
    "membership_id": "membership",
    "invitation_id": "invitation",
    "inventory_item_id": "inventory_item",
    "item_location_id": "item_location",
    "category_id": "category",
    "package_id": "package",
    "conversion_id": "conversion",
    "exception_id": "exception",
    "count_id": "count",
    "product_id": "product",
    "variation_id": "variation",
    "modifier_id": "modifier",
    "kind": "kind",
    "owner_id": "variation",
    "event_id": "waste_event",
    "reason_id": "waste_reason",
    "order_id": "production_order",
    "supplier_id": "supplier",
    "supplier_item_id": "supplier_item",
    "receiving_id": "receiving",
    "line_id": "receiving_line",
    "import_id": "sales_import",
    "digest": "item_file",
    "external_location_id": "external_location",
}


def _request(client, method: str, path: str, ids: dict[str, int]):
    url = path.format(**{name: ids[row] for name, row in PATH_ROWS.items()})
    return client.request(method, url, follow_redirects=False, **CASES[(method, path)](ids))


# --- two businesses, each with one of everything --------------------------------------------------


def _business(db, tenant_id: int, tag: str, code: str) -> dict[str, int]:
    """A location, an item with a lot at risk of expiring, a running markdown, a delivery."""
    location = Location(
        tenant_id=tenant_id, provider="demo", provider_location_id=f"{tag}-1", name=f"{tag} Main"
    )
    db.add(location)
    db.flush()
    square = ProviderConnection(
        tenant_id=tenant_id, provider="square", merchant_id=f"{tag}-M", access_token_enc="x"
    )
    db.add(square)
    db.flush()
    square_place = ExternalLocation(
        tenant_id=tenant_id,
        connection_id=square.id,
        external_location_id=f"{tag}-SQ",
        name=f"{tag} on Square",
    )
    db.add(square_place)
    db.flush()

    def item(slug: str, name: str) -> StockItem:
        row = StockItem(
            tenant_id=tenant_id,
            location_id=location.id,
            slug=slug,
            name=name,
            dimension=Dimension.COUNT,
            stock_unit="each",
            track_mode=TrackMode.MANUAL,
            qty_base=Decimal("24"),
            cost_per_base_cents=Decimal("80"),
            retail_per_base_cents=Decimal("249"),
        )
        db.add(row)
        db.flush()
        return row

    bars = item("bars", f"{tag} protein bars")
    brew = item("brew", f"{tag} cold brew")
    batch = dated(db, bars, 24, date.today() + timedelta(days=9))
    promotion = Promotion(
        tenant_id=tenant_id,
        stock_item_id=brew.id,
        status=PromotionStatus.ACTIVE,
        starts_on=date.today(),
    )
    db.add_all(
        [
            promotion,
            ProductIdentifier(tenant_id=tenant_id, stock_item_id=bars.id, value=code),
        ]
    )

    photo = jpeg(f"{tag} delivery")
    digest = hashlib.sha256(photo).hexdigest()
    key = f"{tenant_id}/{digest}.jpg"
    get_storage().put(key, photo, "image/jpeg")
    document = Document(
        tenant_id=tenant_id,
        location_id=location.id,
        supplier=f"{tag} Foods",
        storage_key=key,
        content_type="image/jpeg",
        sha256=digest,
        byte_size=len(photo),
        status=DocumentStatus.NEEDS_REVIEW,
    )
    db.add(document)
    db.flush()
    line = DocumentLine(
        tenant_id=tenant_id,
        document_id=document.id,
        line_no=1,
        raw_text="2 cs bars",
        parsed_name="bars",
        parsed_qty=Decimal("2"),
    )
    db.add(line)
    snacks = InventoryCategory(tenant_id=tenant_id, name=f"{tag} snacks")
    drinks = InventoryCategory(tenant_id=tenant_id, name=f"{tag} drinks")
    box = ItemPackage(
        tenant_id=tenant_id,
        inventory_item_id=bars.inventory_item_id,
        name="box",
        quantity=Decimal("12"),
        unit="each",
    )
    weighs = ItemUnitConversion(
        tenant_id=tenant_id,
        inventory_item_id=bars.inventory_item_id,
        from_quantity=Decimal("1"),
        from_unit="each",
        to_quantity=Decimal("50"),
        to_unit="g",
    )
    problem = ExceptionRecord(
        tenant_id=tenant_id,
        location_id=location.id,
        type="low_stock",
        severity="warning",
        subject_type="stock_item",
        subject_id=str(bars.id),
        title=f"{tag} bars are low",
    )
    session = StockCount(tenant_id=tenant_id, location_id=location.id, scope="full")
    db.add_all([snacks, drinks, box, weighs, problem, session])
    juice = Product(tenant_id=tenant_id, name=f"{tag} juice")
    ice = Modifier(tenant_id=tenant_id, name="Light ice", effect="scale", scale_factor=1.15)
    db.add_all([juice, ice])
    db.flush()
    size = ProductVariation(tenant_id=tenant_id, product_id=juice.id, name="Regular")
    spilled = WasteReason(tenant_id=tenant_id, code="spilled", label="Spilled")
    db.add_all([size, spilled])
    db.flush()
    wasted = WasteEvent(
        tenant_id=tenant_id,
        location_id=location.id,
        reason_id=spilled.id,
        reason_label="Spilled",
        inventory_item_id=bars.inventory_item_id,
        quantity=Decimal("1"),
        unit="each",
        qty_base=Decimal("1"),
        occurred_at=datetime.now(UTC),
    )
    db.add(wasted)
    db.flush()
    version = save_recipe(
        db,
        OrgContext.system(tenant_id),
        Owner("variation", size.id),
        [NewLine(bars.inventory_item_id, "1", "")],
    )
    run = ProductionOrder(
        tenant_id=tenant_id,
        location_id=location.id,
        inventory_item_id=bars.inventory_item_id,
        recipe_version_id=version.id,
        multiplier=Decimal("1"),
        expected_yield_base=Decimal("1"),
        actual_yield_base=Decimal("1"),
        variance_base=Decimal("0"),
        batch_code=f"{tag}-1",
        occurred_at=datetime.now(UTC),
    )
    db.add(run)
    vendor = Supplier(tenant_id=tenant_id, name=f"{tag} Farms")
    db.add(vendor)
    db.flush()
    sells = SupplierItem(
        tenant_id=tenant_id,
        supplier_id=vendor.id,
        inventory_item_id=bars.inventory_item_id,
        purchase_unit="box",
        pack_size_base=Decimal("12"),
    )
    db.add(sells)
    db.flush()
    delivery = Receiving(tenant_id=tenant_id, location_id=location.id, supplier_id=vendor.id)
    db.add(delivery)
    db.flush()
    arrived = ReceivingLine(
        tenant_id=tenant_id,
        receiving_id=delivery.id,
        inventory_item_id=bars.inventory_item_id,
        arrived_qty=Decimal("2"),
        accepted_qty=Decimal("2"),
        unit="each",
        qty_base=Decimal("2"),
    )
    db.add(arrived)
    db.flush()
    get_storage().put(f"{tenant_id}/imports/iso.csv", SALES_CSV, "text/csv")
    sheet = SalesImport(
        tenant_id=tenant_id,
        location_id=location.id,
        filename="sales.csv",
        sha256="d" * 64,
        storage_key=f"{tenant_id}/imports/iso.csv",
    )
    db.add(sheet)
    db.flush()
    # Each business's item file sits under its own name in its own storage (T2.20-a).
    item_file = hashlib.sha256(f"items-{tenant_id}".encode()).hexdigest()
    get_storage().put(f"{tenant_id}/imports/items-{item_file}.csv", ITEMS_CSV, "text/csv")
    return {
        "item_file": item_file,
        "sales_import": sheet.id,
        "receiving": delivery.id,
        "receiving_line": arrived.id,
        "supplier": vendor.id,
        "supplier_item": sells.id,
        "production_order": run.id,
        "waste_reason": spilled.id,
        "waste_event": wasted.id,
        "kind": "variation",
        "product": juice.id,
        "variation": size.id,
        "modifier": ice.id,
        "count": session.id,
        "exception": problem.id,
        "package": box.id,
        "conversion": weighs.id,
        "inventory_item": bars.inventory_item_id,
        "item_location": bars.item_location_id,
        "category": snacks.id,
        "category2": drinks.id,
        "tenant": tenant_id,
        "location": location.id,
        "external_location": square_place.id,
        "item": bars.id,
        "promo_item": brew.id,
        "batch": batch.id,
        "promotion": promotion.id,
        "document": document.id,
        "line": line.id,
    }


def invitation(db, tenant_id: int, inviter_id: int) -> int:
    """A pending invitation to the business, sent by inviter_id."""
    row = Invitation(
        tenant_id=tenant_id,
        email=f"invited-{tenant_id}@example.test",
        role="employee",
        invited_by_user_id=inviter_id,
        token_hash=hashlib.sha256(f"invitation-{tenant_id}".encode()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(row)
    db.flush()
    return row.id


B_CODE, A_CODE = "990000000017", "110000000014"


@pytest.fixture
def world():
    """Business B exists first; the client is signed in as an owner of business A."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        juniper = Tenant(name="Juniper")
        db.add(juniper)
        db.flush()
        their_owner = User(
            tenant_id=juniper.id,
            email="owner@juniper.test",
            password_hash=hash_password("another-good-password"),
        )
        db.add(their_owner)
        db.flush()
        their_membership = Membership(tenant_id=juniper.id, user_id=their_owner.id, role="owner")
        db.add(their_membership)
        db.flush()
        theirs = _business(db, juniper.id, "Juniper", B_CODE)
        theirs["membership"] = their_membership.id
        theirs["invitation"] = invitation(db, juniper.id, their_owner.id)
        theirs["user"] = their_owner.id
        db.commit()
    with TestClient(app) as client:
        client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "owner@rosewood.test",
                "password": "a-good-password",
            },
        )
        confirm_email("owner@rosewood.test")
        with SessionLocal() as db:
            rosewood = db.scalars(select(Tenant).where(Tenant.name == "Rosewood")).one()
            mine = _business(db, rosewood.id, "Rosewood", A_CODE)
            own = db.scalars(select(Membership).where(Membership.tenant_id == rosewood.id)).one()
            mine["membership"] = own.id
            mine["invitation"] = invitation(db, rosewood.id, own.user_id)
            mine["user"] = own.user_id
            db.commit()
        yield client, mine, theirs


def snapshot(tenant_id: int) -> dict[str, list[tuple]]:
    """Every row the business owns, column by column (the test looks across businesses)."""
    rows = {}
    with SessionLocal() as db:
        for table in Base.metadata.sorted_tables:
            if table.name == "tenants":
                query = select(table).where(table.c.id == tenant_id)
            elif "tenant_id" in table.c:
                query = select(table).where(table.c.tenant_id == tenant_id)
            else:
                continue
            rows[table.name] = sorted(tuple(map(str, row)) for row in db.execute(query))
    return rows


# --- the suite ------------------------------------------------------------------------------------


def test_every_route_that_takes_an_identifier_has_a_case():
    """A new id route fails here until it is added to CASES, and so to the suite."""
    routes = routes_with_identifiers()
    assert len(routes) >= 25, "the route walk must reach the routers' routes"
    assert set(routes) - set(CASES) == set(), "add a case for each new route"
    assert set(CASES) - set(routes) == set(), "remove cases for routes that no longer exist"


@pytest.mark.parametrize(("method", "path"), sorted(CASES), ids=lambda part: str(part))
def test_another_business_identifiers_reach_nothing(world, method, path):
    client, mine, theirs = world
    before = snapshot(theirs["tenant"])

    response = _request(client, method, path, theirs)

    assert response.status_code in (403, 404), response.text[:300]
    assert "Juniper" not in response.text, "the refusal must not describe the other business"
    assert snapshot(theirs["tenant"]) == before, "the other business's rows changed"

    # The same request with the member's own identifiers works, so the refusal above was about
    # whose rows they were, not a broken case.
    own = _request(client, method, path, mine)
    assert own.status_code in (200, 303), own.text[:300]


def test_another_business_barcode_finds_nothing(world):
    """Codes identify products too: B's barcode means nothing to A's scanner."""
    client, _mine, theirs = world
    before = snapshot(theirs["tenant"])

    theirs_found = client.get("/app/scan/lookup", params={"code": B_CODE})
    mine_found = client.get("/app/scan/lookup", params={"code": A_CODE})

    assert theirs_found.status_code == 200 and theirs_found.json()["found"] is False
    assert "Juniper" not in theirs_found.text
    assert mine_found.json()["found"] is True
    assert snapshot(theirs["tenant"]) == before


def test_a_delivery_cannot_be_received_into_another_business_item(world):
    """A's own invoice, with a line pointed at B's item id: nothing of B's moves."""
    client, mine, theirs = world
    before = snapshot(theirs["tenant"])
    line = mine["line"]

    response = client.post(
        f"/app/receiving/{mine['document']}/apply",
        data={
            f"line_{line}_action": "apply",
            f"line_{line}_item": theirs["item"],
            f"line_{line}_qty": "50",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "applied=0" in response.headers["location"]
    assert snapshot(theirs["tenant"]) == before


def test_the_snapshot_sees_a_change(world):
    """Guards the guard: a change to B's rows is detected."""
    _client, _mine, theirs = world
    before = snapshot(theirs["tenant"])
    with SessionLocal() as db:
        db.get(StockItem, theirs["item"]).qty_base = Decimal("23")
        db.commit()
    assert snapshot(theirs["tenant"]) != before
