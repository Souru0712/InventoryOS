"""T3.12: the legacy Square sync is gone (ADR-0085, SQ-11, SCL-06).

A sync reads Square's locations and catalog through the adapter into the external catalog and
nothing else; the hourly read is a job per business; Square's counts are never read; no code path
writes the legacy rows (POS daily totals, sale items, POS-counted stock items).
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select

from app.config import get_settings
from app.costing import Unrecipied, sold_without_recipe
from app.jobs import drain
from app.models import (
    ExternalProduct,
    Job,
    ProductIdentifier,
    SoldItem,
    StockItem,
    SyncRun,
)
from app.orchestration import scheduler, sync
from app.orchestration.dashboard import sales_without_recipe
from app.pos_catalog import read_recently, refresh_catalog
from app.providers.base import NormalizedOrderLine
from app.transform.dashboard import DateRange
from tests.test_order_ingestion import NOW, _read, order, shop  # noqa: F401
from tests.test_pos_catalog import org_of, square  # noqa: F401

APP = Path(__file__).resolve().parents[1] / "app"


def count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_a_sync_reads_only_the_external_catalog_and_never_square_counts(
    db, tenant, square, monkeypatch
):
    _connection, fake = square
    # Even with write-back on, nothing reads Square's counts: the fake has no such call.
    monkeypatch.setattr(get_settings(), "square_write_back", True, raising=False)
    stock_before = count(db, StockItem)
    stats = sync.run_sync(db, org_of(tenant))
    assert stats["products_created"] == 3 and count(db, ExternalProduct) == 3
    assert "MEASUREMENT_UNIT" in fake.types
    assert count(db, SoldItem) == 0, "no legacy sale items"
    assert count(db, StockItem) == stock_before, "no stock item made from the POS"
    assert count(db, ProductIdentifier) == 0
    assert [(r.kind, r.status.value) for r in db.scalars(select(SyncRun))] == [
        ("catalog", "success")
    ]


def test_sync_now_reads_at_once_while_a_burst_of_events_reads_once(db, tenant, square):
    org = org_of(tenant)
    sync.run_sync(db, org)
    sync.run_sync(db, org)
    assert count(db, SyncRun) == 2, "asked for, it reads now"
    assert refresh_catalog(db, org) == {"skipped": 1}, "an event right after waits"
    assert read_recently(db, org) is True
    for run in db.scalars(select(SyncRun)):
        run.started_at = datetime.now(UTC) - timedelta(minutes=5)
    db.commit()
    assert read_recently(db, org) is False


def test_the_hourly_read_is_a_catalog_job_per_connected_business(db, tenant, square):
    scheduler.queue_catalog_refresh()
    scheduler.queue_catalog_refresh()
    jobs = list(db.scalars(select(Job).where(Job.kind == "refresh_catalog")))
    assert [(j.tenant_id, j.status) for j in jobs] == [(tenant.id, "pending")], "merged"
    drain()
    db.expire_all()
    assert count(db, ExternalProduct) == 3 and count(db, SoldItem) == 0

    jobs_registered = BackgroundScheduler()
    scheduler._add_jobs(jobs_registered)
    ids = {job.id for job in jobs_registered.get_jobs()}
    assert "queue_catalog_refresh" in ids and "sync_all_tenants" not in ids


def test_an_unmapped_square_product_is_named_as_square_lists_it(db, tenant, shop):
    """Without legacy sale items, each unmapped POS product stays apart, named from the
    external catalog (AC-RCP-08, CMD-FR-13)."""
    org, pos, connection, _milk, _shot = shop
    for ref, name in (("VAR-BREW", "Cold brew"), ("VAR-CROI", "Croissant")):
        db.add(
            ExternalProduct(
                tenant_id=tenant.id,
                connection_id=connection.id,
                external_variation_id=ref,
                name=name,
                variation_name="16 oz" if ref == "VAR-BREW" else "",
            )
        )
    db.commit()
    pos.orders = [
        order(
            "o-1",
            NormalizedOrderLine("A", "VAR-BREW", "Cold brew - 16 oz", Decimal(2)),
            NormalizedOrderLine("B", "VAR-CROI", "Croissant", Decimal(1)),
        )
    ]
    _read(db, org)
    found = sold_without_recipe(db, org, NOW)
    assert found == [
        Unrecipied("Cold brew 16 oz", None, Decimal(2)),
        Unrecipied("Croissant", None, Decimal(1)),
    ]
    assert count(db, SoldItem) == 0
    note = sales_without_recipe(db, org, DateRange("30d"), NOW)
    assert note is not None and "2 products" in note


def test_no_code_path_writes_the_legacy_pos_rows():
    """No module writes POS daily totals but the demo seed, makes a sale item but the demo, makes
    a POS-counted stock item, or reads Square's counts (T3.12 acceptance: a static check)."""
    sources = {
        path.relative_to(APP).as_posix(): path.read_text(encoding="utf-8")
        for path in APP.rglob("*.py")
    }
    assert not (APP / "load" / "upsert.py").exists()

    def files_matching(pattern: str) -> set[str]:
        return {name for name, text in sources.items() if re.search(pattern, text)}

    assert files_matching(r"record_daily_pos_sales\(") == {"sales.py", "cli.py"}
    assert files_matching(r"(?<!class )\bSoldItem\(") == {"cli.py"}, "only the demo makes one"
    assert not files_matching(r"track_mode\s*=\s*TrackMode\.PROVIDER")
    assert not files_matching(r"inventory/counts/batch-retrieve")
    assert "run_sync" not in sources["orchestration/scheduler.py"]
