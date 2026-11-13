"""Exceptions: one kind of record for everything that needs a person (T1.19; REC-FR-11..16, WF-27).

Pure. An exception is about one subject (an item at a location, a batch, a document, a
connection) and is open at most once per type and subject: a recurring condition updates the open
one rather than opening another (REC-FR-13). Evaluators propose findings; reconcile() decides which
open exceptions a run refreshes, which it opens and which have cleared (REC-FR-14).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.transform.alerts import AlertProposal

CRITICAL, WARNING, INFO = "critical", "warning", "info"
SEVERITIES = (CRITICAL, WARNING, INFO)
STATUSES = ("open", "acknowledged", "resolved", "dismissed")
OPEN_STATUSES = ("open", "acknowledged")
RESOLUTIONS = ("action", "dismissed", "system")


class ExceptionRuleError(ValueError):
    """A change to an exception the rules refuse; nothing is written."""


@dataclass(frozen=True)
class ExceptionType:
    label: str
    severity: str
    fix: str
    # About money: hidden, with every amount, from roles that may not see costs (REC-FR-16).
    financial: bool = False


TYPES: Mapping[str, ExceptionType] = {
    # REC-FR-12; when each is raised and cleared: DOMAIN_MODEL §6.
    "unmapped_product": ExceptionType("Product not mapped", WARNING, "Map the product"),
    "negative_balance": ExceptionType("Below zero", WARNING, "Count the item"),
    "count_variance_high": ExceptionType("Large count variance", WARNING, "Review the count"),
    "batch_expired_unresolved": ExceptionType(
        "Past its date, still in stock", WARNING, "Take it off sale, then count or dispose of it"
    ),
    "batch_shortfall": ExceptionType("No batch left to use", WARNING, "Count the item"),
    "sync_failure": ExceptionType("Sync failed", WARNING, "Check the connection"),
    "pos_reconciliation_mismatch": ExceptionType("POS totals differ", WARNING, "Sync again"),
    "receiving_discrepancy": ExceptionType("Delivery discrepancy", WARNING, "Review the delivery"),
    "price_variance": ExceptionType(
        "Price changed", INFO, "Review the invoice line", financial=True
    ),
    "balance_drift": ExceptionType("Ledger out of balance", CRITICAL, "Contact support"),
    "missing_cost": ExceptionType("Cost missing", WARNING, "Enter the cost", financial=True),
    "conversion_missing": ExceptionType("Unit conversion missing", WARNING, "Add the conversion"),
    "sales_missing": ExceptionType("Sales missing", WARNING, "Enter the day's sales"),
    "sales_source_overlap": ExceptionType("Sales from two sources", WARNING, "Keep one source"),
    # The stock alerts with no REC-FR-12 type of their own (DM-8).
    "out_of_stock": ExceptionType("Out of stock", WARNING, "Receive it, or count it"),
    "low_stock": ExceptionType("Low stock", WARNING, "Reorder it"),
    "stale_count": ExceptionType("Not counted recently", INFO, "Count the item"),
    "waste_risk": ExceptionType("May expire unsold", WARNING, "Sell it first, or mark it down"),
    # An adjustment worth more than the review threshold, posted and awaiting an Owner's or
    # Administrator's review (T1.22, STF-FR-08 R1).
    "adjustment_review": ExceptionType(
        "Adjustment to review", INFO, "Review the adjustment", financial=True
    ),
    # A background job that failed every attempt (T1.20, dead letter); clears on its next success.
    "job_failed": ExceptionType("Background task failed", WARNING, "Contact support"),
}
EXCEPTION_TYPES = tuple(TYPES)

# The five alert kinds and the types they became (DM-8).
FROM_ALERT_KIND: Mapping[str, str] = {
    "out_of_stock": "out_of_stock",
    "low_stock": "low_stock",
    "stale_count": "stale_count",
    "waste_risk": "waste_risk",
    "expired": "batch_expired_unresolved",
}
# The types the stock and expiry evaluation answers for: only these does it auto-resolve.
STOCK_TYPES = frozenset(FROM_ALERT_KIND.values())
EXPIRY_TYPES = frozenset({"waste_risk", "batch_expired_unresolved"})

Key = tuple[str, str, str]  # (type, subject_type, subject_id)


@dataclass(frozen=True)
class Finding:
    """A condition an evaluator found: what the open exception for its key should say."""

    type: str
    subject_type: str
    subject_id: str
    title: str
    location_id: int | None = None
    money_impact_cents: int | None = None
    quantity_base: Decimal | None = None
    days_of_cover: Decimal | None = None
    severity: str | None = None  # the type's, unless the condition makes it worse

    @property
    def key(self) -> Key:
        return (self.type, self.subject_type, self.subject_id)


@dataclass(frozen=True)
class Reconciled:
    opened: list[Finding]
    refreshed: list[Finding]
    cleared: list[Key]


def severity_of(finding: Finding) -> str:
    return finding.severity or TYPES[finding.type].severity


def check_finding(finding: Finding) -> Finding:
    """The finding the rules accept, or ExceptionRuleError."""
    if finding.type not in TYPES:
        raise ExceptionRuleError(f"{finding.type!r} is not a kind of exception")
    if severity_of(finding) not in SEVERITIES:
        raise ExceptionRuleError(f"{finding.severity!r} is not a severity")
    if not finding.subject_type.strip() or not finding.subject_id.strip():
        raise ExceptionRuleError("an exception is about something: name its subject")
    if not finding.title.strip():
        raise ExceptionRuleError("an exception says what is wrong: give it a title")
    return finding


def reconcile(
    open_keys: Iterable[Key], findings: Iterable[Finding], answers_for: frozenset[str]
) -> Reconciled:
    """What one evaluation run changes. A finding whose key is open refreshes it, any other
    opens one; an open exception of a type the run answers for that it no longer finds has
    cleared (REC-FR-14). One finding per key: the first wins."""
    held = set(open_keys)
    seen: dict[Key, Finding] = {}
    for finding in findings:
        seen.setdefault(check_finding(finding).key, finding)
    opened = [finding for key, finding in seen.items() if key not in held]
    refreshed = [finding for key, finding in seen.items() if key in held]
    cleared = sorted(key for key in held if key[0] in answers_for and key not in seen)
    return Reconciled(opened, refreshed, cleared)


def check_dismissal(reason: str | None) -> str:
    """A dismissal says why (AC-REC-08); the reason, tidied."""
    text = " ".join((reason or "").split())
    if not text:
        raise ExceptionRuleError("Say why it is being dismissed.")
    return text[:500]


def visible_to(exception_type: str, can_view_costs: bool) -> bool:
    """Whether a role sees this type at all: financial ones need cost access (REC-FR-16)."""
    return can_view_costs or not TYPES[exception_type].financial


def from_alert(proposal: AlertProposal, location_id: int | None) -> Finding:
    """A stock or expiry alert proposal as the exception it now is (DM-8)."""
    return Finding(
        type=FROM_ALERT_KIND[proposal.kind],
        subject_type="stock_item",
        subject_id=str(proposal.stock_item_id),
        title=proposal.message,
        location_id=location_id,
        money_impact_cents=proposal.value_cents,
        quantity_base=proposal.qty_base,
        days_of_cover=proposal.days_of_cover,
    )


def rank(severity: str) -> int:
    """Most severe first."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def attention_key(
    severity: str, money_cents: int | None, last_seen: float
) -> tuple[int, int, float]:
    """Needs-attention order (CMD-FR-01): most severe, then most money at risk, then latest seen.
    An amount a role may not see is None here, so it cannot reorder that role's list."""
    return (rank(severity), -abs(money_cents or 0), -last_seen)
