"""The plan catalogue (C-41, ADR-0089): plans and what they entitle a business to are data.

A plan names its price at the billing provider and never holds one (E-01, DR-36). Adding,
retiring or repricing a plan is a change to these rows or to the provider, not to code (E-03).
Plans belong to no business, so this module reads them directly; `app/entitlements.py` turns a
business's plan into the answers routes ask for.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.models import BillingPlan, Tenant

COMPLETE, STANDALONE = "complete", "standalone"
# A business made without choosing a plan (a test, a script) has Complete's entitlements: the
# whole product, as a trial had before T4.01.
DEFAULT_PLAN = COMPLETE

# The catalogue a schema built from the models starts with (tests, APP_ENV=test; see
# `_initial_catalogue` in app/models.py). Migration b7d2e9f4c1a6 seeds the same rows on a real
# database; from then on the rows are configuration.
INITIAL_CATALOGUE: tuple[dict, ...] = (
    {
        "code": COMPLETE,
        "name": "Vontry Complete",
        "billing_basis": "per_location",
        "allowed_modes": ["connected", "standalone"],
        "max_locations": None,
        "capability_flags": [],
        "limits": {"invoice_extractions_per_day": 40},
    },
    {
        "code": STANDALONE,
        "name": "Vontry Standalone",
        "billing_basis": "per_business",
        "allowed_modes": ["standalone"],
        "max_locations": 1,
        "capability_flags": [],
        "limits": {"invoice_extractions_per_day": 40},
    },
)


class CatalogueMissing(RuntimeError):
    """The plans table is empty: the database was not migrated or seeded."""


def plan_by_code(db: Session, code: str) -> BillingPlan | None:
    return db.scalars(select(BillingPlan).where(BillingPlan.code == code)).first()


def sellable(db: Session, code: str) -> BillingPlan | None:
    """A plan a business may choose now; a retired plan keeps only those already on it (E-08)."""
    plan = plan_by_code(db, code) if code else None
    return plan if plan is not None and plan.active else None


def plan_for_price(db: Session, price_ref: str | None) -> BillingPlan | None:
    """Which plan a provider price belongs to, so a change made at the provider is understood."""
    if not price_ref:
        return None
    return db.scalars(
        select(BillingPlan).where(BillingPlan.provider_price_ref == price_ref)
    ).first()


def plan_of(tenant: Tenant) -> BillingPlan:
    """The business's plan; the default plan for one made without choosing."""
    if tenant.billing_plan is not None:
        return tenant.billing_plan
    db = object_session(tenant)
    plan = plan_by_code(db, DEFAULT_PLAN) if db is not None else None
    if plan is None:
        raise CatalogueMissing(f"no {DEFAULT_PLAN!r} plan in the catalogue: run the migrations")
    return plan
