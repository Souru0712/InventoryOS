"""Scoped data access: product code reads and changes tenant rows only through here (C-33, DR-09).

Every statement built here is limited to the context's organization and, for location-scoped
rows, to its locations. A model that cannot be limited is refused rather than read unfiltered.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy import ColumnElement, Select, Update, select, update
from sqlalchemy.orm import Session

from app.access.context import OrgContext
from app.db import Base
from app.models import (
    Alert,
    Document,
    DocumentLine,
    EmailLog,
    EmailSuppression,
    ExtractionCorrection,
    HealthSnapshot,
    InventoryCount,
    InventoryEvent,
    Invitation,
    Location,
    Membership,
    ProductIdentifier,
    Promotion,
    ProviderConnection,
    RecipeComponent,
    SoldItem,
    StockAlias,
    StockBatch,
    StockItem,
    SyncRun,
    Tenant,
    User,
    WebhookEvent,
)

M = TypeVar("M", bound=Base)

# Belong to no single organization: users are global identities (SAAS-T-03), reached through
# members() or by name through app.access.system.
GLOBAL_MODELS: frozenset[type[Base]] = frozenset({EmailSuppression, WebhookEvent, User})

# Belong to the organization as a whole, so a location scope does not narrow them.
ORGANIZATION_MODELS: frozenset[type[Base]] = frozenset(
    {
        Tenant,
        Membership,
        Invitation,
        ProviderConnection,
        SoldItem,
        SyncRun,
        EmailLog,
        HealthSnapshot,
    }
)

# Rows whose location is their parent's: (the column naming the parent, the parent model).
LOCATION_PATHS: dict[type[Base], tuple[str, type[Base]]] = {
    **{
        model: ("stock_item_id", StockItem)
        for model in (
            Alert,
            InventoryCount,
            InventoryEvent,
            ProductIdentifier,
            Promotion,
            RecipeComponent,
            StockAlias,
            StockBatch,
        )
    },
    DocumentLine: ("document_id", Document),
    ExtractionCorrection: ("document_id", Document),
}


class UnscopedAccess(Exception):
    """A statement that cannot be limited to the context's organization and locations."""


def scope_filter(org: OrgContext, model: type[Base]) -> list[ColumnElement[bool]]:
    """The WHERE clauses that keep a statement on model inside org."""
    name = model.__name__
    if model in GLOBAL_MODELS:
        raise UnscopedAccess(f"{name} belongs to no organization; use app.access.system")
    columns = model.__table__.c
    if model is Tenant:
        clauses = [columns.id == org.tenant_id]
    elif "tenant_id" in columns:
        clauses = [columns.tenant_id == org.tenant_id]
    else:
        raise UnscopedAccess(f"{name} has no tenant_id")

    if org.location_ids is None or model in ORGANIZATION_MODELS:
        return clauses
    allowed = sorted(org.location_ids)
    if model is Location:
        clauses.append(columns.id.in_(allowed))
    elif "location_id" in columns:
        clauses.append(columns.location_id.in_(allowed))
    elif model in LOCATION_PATHS:
        column, parent = LOCATION_PATHS[model]
        parents = parent.__table__.c
        clauses.append(
            columns[column].in_(
                select(parents.id).where(
                    parents.tenant_id == org.tenant_id, parents.location_id.in_(allowed)
                )
            )
        )
    else:
        # Deny by default: a location-limited reader gets no rows whose location is not known.
        raise UnscopedAccess(f"{name} has no location path for a location-limited context")
    return clauses


def select_scoped(org: OrgContext, model: type[M]) -> Select[M]:
    """select(model), limited to org."""
    return select(model).where(*scope_filter(org, model))


def get_scoped(db: Session, org: OrgContext, model: type[M], ident: int) -> M | None:
    """The row with this id, or None when it does not exist or lies outside org."""
    return db.scalars(select_scoped(org, model).where(model.__table__.c.id == ident)).first()


def members(db: Session, org: OrgContext) -> list[User]:
    """The people with an active membership in org whose accounts are active."""
    return list(
        db.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.tenant_id == org.tenant_id,
                Membership.status == "active",
                User.is_active.is_(True),
            )
            .order_by(User.id)
        )
    )


def team(db: Session, org: OrgContext) -> list[tuple[Membership, User]]:
    """Every membership of org, active or revoked, with its person, oldest first (T1.10)."""
    rows = db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == org.tenant_id)
        .order_by(Membership.id)
    )
    return [(membership, user) for membership, user in rows]


def update_scoped(org: OrgContext, model: type[Base]) -> Update:
    """update(model), limited to org."""
    return update(model).where(*scope_filter(org, model))
