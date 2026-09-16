"""Operator CLI. This is what you use to onboard the first customers by hand."""
from __future__ import annotations

import argparse
import logging
import random
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.config import require_deployable
from app.db import init_db, run_migrations, session_scope, stamp_migrations
from app.models import (
    EmailSuppression,
    InventoryCount,
    Location,
    IdentifierType,
    ProductIdentifier,
    RecipeComponent,
    SalesDaily,
    SoldItem,
    StockBatch,
    StockItem,
    Tenant,
    TrackMode,
    User,
)
from app.monitoring import init_monitoring
from app.notify.email import suppress, unsuppress
from app.orchestration import scheduler
from app.orchestration.alerts import evaluate_and_store, send_alert_digest
from app.orchestration.health import current as current_health, record_snapshot
from app.orchestration.reporting import send_weekly_report, weekly_report
from app.orchestration.receiving import parse_document
from app.orchestration.sync import run_sync
from app.orchestration.views import refresh_derived_quantities
from app.security import hash_password
from app.transform.reports import money
from app.transform.barcodes import check_digit
from app.units import Dimension, to_base

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stockpulse")

# One café: things you count, things you weigh, things you pour.
# Quantities are yesterday morning's physical count, so today's sales draw them down.
DEMO_STOCK = [
    # (name, category, dimension, unit, counted, reorder, par, cost per unit, receive unit, pack)
    ("House Blend beans 1kg bag", "Coffee", Dimension.COUNT, "each", 14, 6, 24, "12.50", "each", "12"),
    ("Oat milk 1L carton", "Dairy", Dimension.COUNT, "each", 9, 12, 36, "2.10", "each", "12"),
    ("Takeaway cups 12oz", "Packaging", Dimension.COUNT, "each", 420, 300, 1000, "0.11", "each", "500"),
    ("Espresso beans (hopper stock)", "Coffee", Dimension.WEIGHT, "kg", "8", "6", "30", "18.00", "kg", "6"),
    ("Pastry flour", "Bakery", Dimension.WEIGHT, "kg", "45", "10", "50", "1.35", "kg", "25"),
    ("Sliced ham", "Deli", Dimension.WEIGHT, "kg", "3.2", "2.5", "8", "9.80", "kg", "2"),
    ("Whole milk", "Dairy", Dimension.VOLUME, "l", "60", "20", "80", "0.95", "l", "6"),
    ("Vanilla syrup", "Syrups", Dimension.VOLUME, "l", "1.6", "1.5", "6", "8.40", "l", "1"),
    ("Cold brew concentrate", "Coffee", Dimension.VOLUME, "l", "18", "4", "20", "6.20", "l", "5"),
]

DEMO_MENU = [
    # (name, category, price cents, recipe: [(stock name, qty, unit)], daily units)
    ("Latte", "Drinks", 425, [("Whole milk", "220", "ml"), ("Espresso beans (hopper stock)", "18", "g")], 95),
    ("Flat white", "Drinks", 415, [("Whole milk", "150", "ml"), ("Espresso beans (hopper stock)", "18", "g")], 60),
    ("Vanilla latte", "Drinks", 470, [("Whole milk", "220", "ml"), ("Espresso beans (hopper stock)", "18", "g"), ("Vanilla syrup", "20", "ml")], 22),
    ("Iced cold brew", "Drinks", 450, [("Cold brew concentrate", "120", "ml"), ("Whole milk", "60", "ml")], 34),
    ("Filter coffee", "Drinks", 300, [("Espresso beans (hopper stock)", "16", "g")], 40),
    ("Ham and cheese croissant", "Food", 550, [("Sliced ham", "45", "g"), ("Pastry flour", "70", "g")], 26),
    ("Butter croissant", "Food", 380, [("Pastry flour", "65", "g")], 38),
    ("Retail beans 1kg", "Retail", 1800, [("House Blend beans 1kg bag", "1", "each")], 3),
    ("Oat milk to go", "Retail", 320, [("Oat milk 1L carton", "1", "each")], 4),
]


# Retail barcodes for the countable goods. The check digit is computed, so a scan of one of
# these is not flagged as a misread.
DEMO_CODES = {
    "House Blend beans 1kg bag": "01234500001",
    "Oat milk 1L carton": "01234500002",
    "Takeaway cups 12oz": "01234500003",
}

# What the retail lines sell for. Ingredients have no shelf price, so they never get marked down.
DEMO_RETAIL = {
    "Oat milk 1L carton": "3.20",
    "House Blend beans 1kg bag": "18.00",
}

DEMO_LOTS = [
    # (stock name, share of what is on hand, days until best-before): one of every expiry state
    ("Cold brew concentrate", "1", 2),  # will not all sell in time
    ("Oat milk 1L carton", "1", 1),  # will not all sell in time
    ("Whole milk", "0.4", 1),  # sells through: the lot to use first
    ("Whole milk", "0.6", 6),
    ("House Blend beans 1kg bag", "1", 3),  # a retail line worth marking down
    ("Sliced ham", "0.4", -1),  # past its date and still on the shelf
    ("Sliced ham", "0.6", 4),
]

def cmd_init_db(_: argparse.Namespace) -> int:
    init_db()
    print("schema is at head")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    if args.stamp:
        # For a database created before Alembic existed: record it as already migrated.
        stamp_migrations(args.revision)
        print(f"stamped at {args.revision}")
        return 0
    run_migrations()
    print("migrated to head")
    return 0


def cmd_scheduler(_: argparse.Namespace) -> int:
    """Run the scheduled jobs until stopped. Deployed, this process is the only place they run."""
    init_monitoring("scheduler")
    require_deployable()
    init_db()
    return scheduler.run_forever()


def cmd_email(args: argparse.Namespace) -> int:
    """Stop or resume sending to an address, and see what is currently suppressed."""
    with session_scope() as db:
        if args.suppress:
            entry = suppress(db, args.suppress, args.reason or "suppressed by hand", source="manual")
            print(f"suppressed {entry.email}: {entry.reason}")
            return 0
        if args.allow:
            print(f"sending to {args.allow} again" if unsuppress(db, args.allow)
                  else f"{args.allow} was not suppressed")
            return 0
        rows = list(db.scalars(select(EmailSuppression).order_by(EmailSuppression.created_at)))
        for row in rows:
            print(f"{row.email:<40} {row.source:<10} {row.reason or ''}")
        print(f"{len(rows)} suppressed address{'es' if len(rows) != 1 else ''}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    with session_scope() as db:
        stats = run_sync(db, args.tenant, days_back=args.days)
    print(f"tenant {args.tenant}: {stats}")
    return 0


def cmd_alerts(args: argparse.Namespace) -> int:
    with session_scope() as db:
        refresh_derived_quantities(db, args.tenant)
        stats = evaluate_and_store(db, args.tenant)
        if args.send:
            stats["emailed"] = send_alert_digest(db, args.tenant)
    print(f"tenant {args.tenant}: {stats}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    with session_scope() as db:
        if args.send:
            sent = send_weekly_report(db, args.tenant)
            print(f"weekly report sent: {sent}")
            return 0
        report = weekly_report(db, args.tenant)
    print(f"week {report.period_start} to {report.period_end}")
    print(f"  net sales        {money(report.net_cents)}")
    print(f"  inventory value  {money(report.inventory_value_cents)}")
    print(f"  alerts           {len(report.alerts)}")
    for roll in report.by_dimension:
        print(f"  {roll.dimension:<7} {roll.item_count:>3} items  {money(roll.value_cents):>10}  {roll.at_risk} at risk")
    for alert in report.alerts[:10]:
        print(f"  ! {alert.message}")
    return 0


def cmd_parse_doc(args: argparse.Namespace) -> int:
    with session_scope() as db:
        document = parse_document(db, args.document)
        print(f"document {document.id}: {document.status.value}, {len(document.lines)} lines")
        for line in sorted(document.lines, key=lambda row: row.line_no):
            print(
                f"  {line.line_no:>2}. {line.parsed_name[:38]:<38} "
                f"{line.status.value:<10} {line.match_reason or ''}"
            )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Seed a realistic café so you can demo before anyone connects Square."""
    init_db()
    random.seed(7)
    today = datetime.now(timezone.utc).date()

    with session_scope() as db:
        if db.scalars(select(User).where(User.email == args.email.lower())).first():
            print(f"{args.email} already exists; delete the database to reseed")
            return 1

        tenant = Tenant(name=args.name)
        db.add(tenant)
        db.flush()
        db.add(
            User(
                tenant_id=tenant.id,
                email=args.email.lower(),
                password_hash=hash_password(args.password),
            )
        )
        location = Location(
            tenant_id=tenant.id,
            provider="demo",
            provider_location_id="demo-1",
            name=f"{args.name} - Main",
            timezone="UTC",
        )
        db.add(location)
        db.flush()

        stock: dict[str, StockItem] = {}
        anchor = datetime.combine(today - timedelta(days=1), datetime.min.time(), timezone.utc)
        for name, category, dimension, unit, on_hand, reorder, par, cost, recv, pack in DEMO_STOCK:
            qty_base = to_base(on_hand, unit)
            item = StockItem(
                tenant_id=tenant.id,
                location_id=location.id,
                slug=name.lower().replace(" ", "-")[:160],
                name=name,
                category=category,
                dimension=dimension,
                stock_unit=unit,
                track_mode=TrackMode.MANUAL if dimension is Dimension.COUNT else TrackMode.DERIVED,
                qty_base=qty_base,
                reorder_point_base=to_base(reorder, unit),
                par_level_base=to_base(par, unit),
                cost_per_base_cents=(Decimal(cost) * 100) / to_base("1", unit),
                receive_unit=recv,
                pack_size_base=to_base(pack, unit),
                supplier="Northside Supply",
                lead_time_days=3,
                last_counted_at=anchor,
            )
            db.add(item)
            db.flush()
            db.add(
                InventoryCount(
                    tenant_id=tenant.id,
                    stock_item_id=item.id,
                    qty_base=qty_base,
                    source="opening",
                    note="demo opening count",
                    counted_at=anchor,
                )
            )
            stock[name] = item
            if price := DEMO_RETAIL.get(name):
                item.retail_per_base_cents = (Decimal(price) * 100) / to_base("1", unit)
            if body := DEMO_CODES.get(name):
                db.add(
                    ProductIdentifier(
                        tenant_id=tenant.id,
                        stock_item_id=item.id,
                        identifier_type=IdentifierType.UPC,
                        value=body + check_digit(body),
                        source="manual",
                    )
                )

        sold: dict[str, SoldItem] = {}
        for name, category, price, recipe, _daily in DEMO_MENU:
            item = SoldItem(
                tenant_id=tenant.id,
                provider="demo",
                provider_item_id=f"demo-{name.lower().replace(' ', '-')}",
                name=name,
                category=category,
                price_cents=price,
            )
            db.add(item)
            db.flush()
            sold[name] = item
            for stock_name, quantity, unit in recipe:
                db.add(
                    RecipeComponent(
                        tenant_id=tenant.id,
                        sold_item_id=item.id,
                        stock_item_id=stock[stock_name].id,
                        qty_base_per_sale=to_base(quantity, unit),
                    )
                )

        for offset in range(args.days):
            day = today - timedelta(days=offset)
            weekend = day.weekday() >= 5
            for name, _category, price, _recipe, daily in DEMO_MENU:
                factor = 1.35 if weekend else 1.0
                units = max(0, int(random.gauss(daily * factor, daily * 0.18)))
                if units == 0:
                    continue
                gross = units * price
                db.add(
                    SalesDaily(
                        tenant_id=tenant.id,
                        sold_item_id=sold[name].id,
                        location_id=location.id,
                        dt=day,
                        units=Decimal(units),
                        gross_cents=gross,
                        net_cents=int(gross * 0.96),
                    )
                )
        db.flush()

        refresh_derived_quantities(db, tenant.id, today)
        # Dated after the refresh, so each lot is a share of what is actually left.
        for name, share, days in DEMO_LOTS:
            item = stock[name]
            step = Decimal("1") if item.dimension is Dimension.COUNT else Decimal("0.0001")
            db.add(
                StockBatch(
                    tenant_id=tenant.id,
                    stock_item_id=item.id,
                    received_qty_base=(Decimal(item.qty_base) * Decimal(share)).quantize(step),
                    expires_on=today + timedelta(days=days),
                    source="label",
                )
            )
        db.flush()
        stats = evaluate_and_store(db, tenant.id)
        record_snapshot(db, tenant.id)
        tenant_id = tenant.id

    print(f"seeded tenant {tenant_id} ({args.name})")
    print(f"  log in: {args.email} / {args.password}")
    print(f"  alerts: {stats}")
    print("  run: python -m app.cli report --tenant %s" % tenant_id)
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Print the health score and its reasons; --snapshot also keeps tonight's record."""
    with session_scope() as db:
        if args.snapshot:
            row = record_snapshot(db, args.tenant)
            print(f"snapshot kept for {row.dt}: {row.score}")
        health = current_health(db, args.tenant)
    report, actions = health.report, health.actions
    print(f"inventory health: {report.score if report.score is not None else '-'} ({report.band_label})")
    for finding in report.headline:
        print(f"  [{finding.tone}] {finding.text}")
    for action in actions.urgent + actions.recommended:
        print(f"  {action.tier}: {action.text}")
    print(f"  {actions.healthy_items} healthy")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stockpulse")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create or upgrade the schema").set_defaults(func=cmd_init_db)

    p = sub.add_parser("migrate", help="run database migrations")
    p.add_argument("--stamp", action="store_true", help="mark an existing schema as migrated")
    p.add_argument("--revision", default="head")
    p.set_defaults(func=cmd_migrate)

    sub.add_parser(
        "scheduler", help="run scheduled jobs; one holds the lock, any others stand by"
    ).set_defaults(func=cmd_scheduler)

    p = sub.add_parser("email", help="see or change suppressed email addresses")
    p.add_argument("--suppress", help="stop sending to this address")
    p.add_argument("--allow", help="send to this address again")
    p.add_argument("--reason", help="why, when suppressing")
    p.set_defaults(func=cmd_email)

    p = sub.add_parser("sync", help="pull Square data for one tenant")
    p.add_argument("--tenant", type=int, required=True)
    p.add_argument("--days", type=int, default=60)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("alerts", help="re-evaluate low stock")
    p.add_argument("--tenant", type=int, required=True)
    p.add_argument("--send", action="store_true", help="email the digest")
    p.set_defaults(func=cmd_alerts)

    p = sub.add_parser("report", help="weekly report")
    p.add_argument("--tenant", type=int, required=True)
    p.add_argument("--send", action="store_true", help="email it")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("parse-doc", help="re-read an uploaded invoice")
    p.add_argument("--document", type=int, required=True)
    p.set_defaults(func=cmd_parse_doc)

    p = sub.add_parser("health", help="inventory health score and today's actions")
    p.add_argument("--tenant", type=int, required=True)
    p.add_argument("--snapshot", action="store_true", help="also keep tonight's record")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("demo", help="seed a demo café with all three stock types")
    p.add_argument("--email", default="demo@stockpulse.local")
    p.add_argument("--password", default="demopassword")
    p.add_argument("--name", default="Rosewood Coffee")
    p.add_argument("--days", type=int, default=42)
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
