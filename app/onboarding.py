"""The onboarding checklist (T2.20; SAAS §5, CMD-FR-11).

Each step is done when the business has what the step makes, read from its own records, so the
checklist never disagrees with the product and a step done any other way counts too. Onboarding
never blocks anything: a partly set-up business works, and the dashboard shows what is left.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Numeric, cast
from sqlalchemy.orm import Session

from app.access import OrgContext, members, select_scoped
from app.models import (
    AuditEvent,
    InventoryItem,
    Invitation,
    ItemLocation,
    Location,
    ProviderConnection,
    Receiving,
    Recipe,
    SalesSource,
    StockCount,
    Supplier,
)


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    hint: str
    link: str
    done: bool


def _any(db: Session, query) -> bool:
    return db.scalars(query.limit(1)).first() is not None


def checklist(db: Session, org: OrgContext) -> list[Step]:
    """SAAS §5 steps 2 to 10, in order, each with where to do it."""
    people = len(members(db, org))
    return [
        Step(
            "location",
            "Set up your location",
            "Address, time zone and when your business day ends.",
            "/app/locations",
            _any(db, select_scoped(org, Location)),
        ),
        Step(
            "items",
            "Add your items",
            "Import a CSV, or type them into the quick-add grid.",
            "/app/catalog/import",
            _any(db, select_scoped(org, InventoryItem)),
        ),
        Step(
            "suppliers",
            "Add your suppliers",
            "Name, lead time and delivery days.",
            "/app/suppliers",
            _any(db, select_scoped(org, Supplier)),
        ),
        Step(
            "recipes",
            "Create recipes for your top sellers",
            "What each product uses, so sales take ingredients out of stock.",
            "/app/products",
            _any(db, select_scoped(org, Recipe)),
        ),
        Step(
            "sales",
            "Choose how sales arrive",
            "Connect a POS, enter each day's sales, or import a sales file.",
            "/app/sales/daily",
            _any(db, select_scoped(org, SalesSource))
            or _any(
                db,
                select_scoped(org, ProviderConnection).where(ProviderConnection.status == "active"),
            ),
        ),
        Step(
            "alerts",
            "Set your alerts",
            "Reorder points, expiry warnings and when the alert email goes out.",
            "/app/settings/alerts",
            _any(
                db,
                select_scoped(org, AuditEvent).where(
                    AuditEvent.action.in_(("settings.alerts_changed", "settings.expiry_changed"))
                ),
            )
            or _any(
                db,
                select_scoped(org, ItemLocation).where(
                    cast(ItemLocation.reorder_point_base, Numeric) > 0
                ),
            ),
        ),
        Step(
            "count",
            "Count your stock",
            "One count gives every item its real starting quantity.",
            "/app/counts",
            _any(db, select_scoped(org, StockCount).where(StockCount.status == "posted")),
        ),
        Step(
            "receiving",
            "Receive a delivery",
            "Check it in against the invoice.",
            "/app/deliveries",
            _any(db, select_scoped(org, Receiving).where(Receiving.status == "posted")),
        ),
        Step(
            "team",
            "Invite your team",
            "Each person signs in with their own account and role.",
            "/app/team",
            people > 1 or _any(db, select_scoped(org, Invitation)),
        ),
    ]
