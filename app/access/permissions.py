"""Permission keys and the R1 role presets (USER_ROLES_AND_PERMISSIONS §2-3, PD-11, STF-FR-02).

A role is a named set of keys. A grant the matrix marks "own records within the undo window" is
left out until the operation that enforces the window exists; a key not listed here does not
exist, so asking for it is a programming error, not a quiet denial.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VIEW_COSTS = "inventory.view_costs"

# Every R1 key with the operation it guards. R1.1, R2 and Expansion keys arrive with their work.
PERMISSIONS: Mapping[str, str] = {
    "inventory.view": "Items, quantities, batches, history",
    VIEW_COSTS: "Unit costs, values, cost snapshots",
    "item.edit": "Items, units, conversions, levels, categories, storage areas",
    "product.edit": "Sellable products, variations, modifiers, markdowns",
    "recipe.view": "Recipe cards and instructions",
    "recipe.edit": "Product recipes, modifier effects",
    "production_recipe.edit": "Production recipes (yield, shelf life)",
    "production.record": "Record or complete a production order",
    "production.void": "Void a production order",
    "receiving.record": "Start receiving, upload invoice, enter quantities",
    "receiving.post": "Post a receiving",
    "receiving.edit_costs": "Enter or override costs, resolve price variances",
    "receiving.correct": "Correct a posted receiving",
    "supplier.manage": "Suppliers and supplier items",
    "reorder.view": "Reorder suggestions",
    "waste.record": "Log waste",
    "waste.void": "Void waste",
    "count.record": "Run and post a count, date stock on the shelf",
    "count.review": "Review and approve variances, set reasons",
    "adjustment.create": "Manual adjustment",
    "adjustment.override_negative": "Allow a manual movement to cross zero",
    "sales.record": "Enter or correct daily sales and usage",
    "sales.import": "Import sales files, manage column mappings",
    "exception.view": "See exceptions",
    "exception.resolve": "Acknowledge, resolve, dismiss",
    "reports.operational": "Consumption, counts, production, waste quantities, health",
    "reports.financial": "Valuation, waste cost, margins, variance cost, purchasing",
    "data.export": "CSV exports",
    "mapping.manage": "POS and import product mapping, sync, reprocess consumption",
    "pos.manage": "Connect or disconnect a POS, location mapping, operating mode",
    "alerts.configure": "Thresholds, expiry window, recipients",
    "location.manage": "Create and archive locations, time zone, open days",
    "settings.manage": "Business profile, reasons, units",
    "team.manage": "Invite, change role or scope, revoke",
    "owner.manage": "Add or remove Owners, transfer ownership",
    "subscription.manage": "Plan, location quantity, payment method, cancel",
    "audit.view": "Business audit history",
    "account.delete": "Request organization deletion",
}

_EVERYONE = {"inventory.view", "recipe.view", "exception.view"}
_READ_ONLY = _EVERYONE | {VIEW_COSTS, "reorder.view", "reports.operational", "reports.financial"}
_EMPLOYEE = _EVERYONE | {
    "production.record",
    "receiving.record",
    "receiving.post",  # PD-19: whoever receives, posts
    "reorder.view",
    "waste.record",
    "count.record",
}
_MANAGER = (
    _READ_ONLY
    | _EMPLOYEE
    | {
        "item.edit",
        "product.edit",
        "recipe.edit",
        "production_recipe.edit",
        "production.void",
        "receiving.edit_costs",
        "receiving.correct",
        "supplier.manage",
        "waste.void",
        "count.review",
        "adjustment.create",
        "adjustment.override_negative",
        "sales.record",
        "sales.import",
        "exception.resolve",
        "data.export",
        "mapping.manage",
        "alerts.configure",
        "audit.view",
    }
)
_ADMINISTRATOR = _MANAGER | {
    "pos.manage",
    "location.manage",
    "settings.manage",
    "team.manage",
}
_OWNER = _ADMINISTRATOR | {"owner.manage", "subscription.manage", "account.delete"}

ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    "owner": frozenset(_OWNER),
    "administrator": frozenset(_ADMINISTRATOR),
    "manager": frozenset(_MANAGER),
    "employee": frozenset(_EMPLOYEE),
    "read_only": frozenset(_READ_ONLY),
}

ALL_PERMISSIONS: frozenset[str] = frozenset(PERMISSIONS)

# Response fields a role without VIEW_COSTS never receives (USER_ROLES_AND_PERMISSIONS §5).
COST_FIELDS = frozenset(
    {"cost_cents", "cost_per_base_cents", "value_cents", "unit_price_cents", "line_total_cents"}
)


def role_permissions(role: str) -> frozenset[str]:
    """The keys a role holds; an unknown role holds none."""
    return ROLE_PERMISSIONS.get(role, frozenset())


def known(key: str) -> str:
    """The key, after checking it exists, so a typo fails at import rather than denying."""
    if key not in PERMISSIONS:
        raise KeyError(f"unknown permission {key!r}")
    return key


def without_costs(payload: Any) -> Any:
    """A copy of a JSON-ready payload with every cost and value field removed."""
    if isinstance(payload, dict):
        return {
            key: without_costs(value) for key, value in payload.items() if key not in COST_FIELDS
        }
    if isinstance(payload, list):
        return [without_costs(value) for value in payload]
    return payload
