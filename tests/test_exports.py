"""T4.05: the full data export (ADM-FR-10, SAAS-D-01, SAAS-D-02, AC-ADM-03).

A zip of CSVs and invoice files covering every entity in SAAS-D-01, made by a job, also in
read-only state; only the person who asked downloads it, until it expires and is deleted.
"""

import csv
import io
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.access import OrgContext
from app.access.permissions import VIEW_COSTS, role_permissions
from app.access.system import expired_exports
from app.db import SessionLocal
from app.exports import TABLES, ExportRefused, build, expire, request_export
from app.jobs import _handlers, drain
from app.models import DataExport, EmailLog, Location, SubscriptionStatus, Tenant, User
from app.storage import StorageError, get_storage
from tests.test_audit import corner, member  # noqa: F401  (the fixture, by name)
from tests.test_isolation import A_CODE, _business

# Each entity SAAS-D-01 names, and the file that holds it.
SAAS_D_01 = {
    "items": "inventory_items.csv",
    "conversions": "item_unit_conversions.csv",
    "suppliers": "suppliers.csv",
    "recipes": "recipes.csv",
    "recipe versions": "recipe_versions.csv",
    "movements": "inventory_movements.csv",
    "batches": "stock_batches.csv",
    "receivings": "receivings.csv",
    "waste": "waste_events.csv",
    "counts": "stock_counts.csv",
    "exceptions": "exceptions.csv",
    "sales": "sales_transactions.csv",
}


def _rows(archive: zipfile.ZipFile, name: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8"))))


@pytest.fixture
def shop(db, tenant):
    """A business with one of everything, as the isolation suite builds it."""
    ids = _business(db, tenant.id, "Rosewood", A_CODE)
    owner = db.scalars(select(User).where(User.tenant_id == tenant.id)).first()
    db.commit()
    org = OrgContext(tenant_id=tenant.id, user_id=owner.id, permissions=role_permissions("owner"))
    return org, owner, ids


def _export(db, org, owner) -> zipfile.ZipFile:
    row = request_export(db, org, owner.id)
    db.commit()
    drain(handlers={"export_data": _handlers()["export_data"]})
    db.expire_all()
    row = db.get(DataExport, row.id)
    assert row.status == "ready", row.error
    return zipfile.ZipFile(io.BytesIO(get_storage().get(row.storage_key)))


def test_the_export_covers_every_entity_saas_d_01_names(db, shop):
    """AC-ADM-03: a zip of CSVs and invoice files covering every entity in SAAS-D-01."""
    org, owner, ids = shop
    archive = _export(db, org, owner)
    names = set(archive.namelist())

    assert {f"{table}.csv" for table in TABLES} <= names
    assert set(SAAS_D_01.values()) <= names
    assert f"invoices/{ids['document']}.jpg" in names, "the invoice photo itself"
    assert "README.txt" in names
    assert _rows(archive, "inventory_items.csv"), "the business's items are in it"
    assert _rows(archive, "receivings.csv") and _rows(archive, "documents.csv")


def test_only_this_business_and_never_a_secret(db, shop):
    org, owner, _ids = shop
    other = Tenant(name="Juniper")
    db.add(other)
    db.flush()
    db.add(Location(tenant_id=other.id, name="Juniper Main", timezone="UTC"))
    db.commit()

    archive = _export(db, org, owner)
    places = {row["name"] for row in _rows(archive, "locations.csv")}
    assert "Juniper Main" not in places and places, "another business's rows never appear"
    for name in archive.namelist():
        if name.endswith(".csv"):
            header = archive.read(name).decode("utf-8").splitlines()[0]
            assert not any(word in header for word in ("_enc", "token", "password", "hash")), name


def test_someone_without_costs_gets_no_money_columns(db, shop):
    _org, owner, _ids = shop
    tenant_id = _org.tenant_id
    org = OrgContext(
        tenant_id=tenant_id,
        user_id=owner.id,
        permissions=role_permissions("owner") - {VIEW_COSTS},
    )
    archive = _export(db, org, owner)
    header = archive.read("stock_items.csv").decode("utf-8").splitlines()[0]
    assert "cost_per_base_cents" not in header and "name" in header
    assert "Costs, prices and values are left out" in archive.read("README.txt").decode()


def test_a_full_export_needs_every_location(db, shop):
    org, owner, ids = shop
    limited = OrgContext(
        tenant_id=org.tenant_id,
        user_id=owner.id,
        location_ids=frozenset({ids["location"]}),
        permissions=org.permissions,
    )
    with pytest.raises(ExportRefused, match="every location"):
        request_export(db, limited, owner.id)


def test_asking_twice_while_it_is_prepared_queues_one(db, shop):
    org, owner, _ids = shop
    first = request_export(db, org, owner.id)
    again = request_export(db, org, owner.id)
    assert first.id == again.id


def test_the_person_is_emailed_when_it_is_ready(db, shop):
    org, owner, _ids = shop
    _export(db, org, owner)
    logged = db.scalars(select(EmailLog).where(EmailLog.kind == "data_export")).one()
    assert logged.to_email == owner.email


def test_a_repeat_of_the_job_writes_nothing_new(db, shop):
    org, owner, _ids = shop
    _export(db, org, owner)
    row = db.scalars(select(DataExport)).one()
    key, size = row.storage_key, row.size_bytes
    build(db, OrgContext.system(org.tenant_id), row.id)
    assert (row.storage_key, row.size_bytes, row.status) == (key, size, "ready")


def test_an_expired_export_loses_its_file(db, shop):
    org, owner, _ids = shop
    _export(db, org, owner)
    row = db.scalars(select(DataExport)).one()
    key = row.storage_key
    later = datetime.now(UTC) + timedelta(days=4)
    assert expire(db, expired_exports(db, later)) == 1
    assert row.status == "expired" and row.storage_key is None
    with pytest.raises(StorageError):
        get_storage().get(key)


# --- the pages: read-only included, the asker only -------------------------------------------------


def test_a_read_only_business_exports_and_downloads(corner):  # noqa: F811
    owner, _clients = corner
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant)).one()
        tenant.subscription_status = SubscriptionStatus.CANCELED
        db.commit()
    assert owner.post("/app/exports", follow_redirects=False).status_code == 303
    drain(handlers={"export_data": _handlers()["export_data"]})
    with SessionLocal() as db:
        export_id = db.scalars(select(DataExport)).one().id
    page = owner.get("/app/exports").text
    assert f"/app/exports/{export_id}/download" in page
    download = owner.get(f"/app/exports/{export_id}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]
    assert "inventory_items.csv" in zipfile.ZipFile(io.BytesIO(download.content)).namelist()


def test_only_the_person_who_asked_downloads_it(corner):  # noqa: F811
    owner, clients = corner
    owner.post("/app/exports", follow_redirects=False)
    drain(handlers={"export_data": _handlers()["export_data"]})
    with SessionLocal() as db:
        export_id = db.scalars(select(DataExport)).one().id
    administrator = member(clients, "ada@corner.test", "administrator")
    employee = member(clients, "eve@corner.test", "employee")
    assert administrator.get(f"/app/exports/{export_id}/download").status_code == 404
    assert "Asked for by someone else" in administrator.get("/app/exports").text
    assert employee.get("/app/exports").status_code == 403
