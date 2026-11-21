"""Multi-tenant schema. Every business row carries tenant_id."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, object_session, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base, Qty, UTCDateTime
from app.transform.adjustments import ADJUSTMENT_REASONS
from app.transform.batches import BATCH_ORDERS
from app.transform.costs import COST_SOURCES
from app.transform.counts import COUNT_SCOPES, COUNT_STATUSES, VARIANCE_REASONS
from app.transform.exceptions import EXCEPTION_TYPES, RESOLUTIONS, SEVERITIES, STATUSES
from app.transform.jobs import JOB_KINDS
from app.transform.jobs import STATUSES as JOB_STATUSES
from app.transform.ledger import (
    ALLOCATION_MODES,
    DECREASES,
    EITHER_WAY,
    INCREASES,
    MOVEMENT_TYPES,
    TRANSACTION_KINDS,
)
from app.transform.mappings import MAPPING_STATUSES
from app.transform.onboarding import ANSWERS as ONBOARDING_ANSWERS
from app.transform.production import STATUSES as PRODUCTION_STATUSES
from app.transform.products import MODIFIER_EFFECTS, PRODUCTION_MODES
from app.transform.receivings import LINE_STATUSES as RECEIVING_LINE_STATUSES
from app.transform.receivings import STATUSES as RECEIVING_STATUSES
from app.transform.sales import (
    CONSUMPTION_STATUSES,
    RETURN_STATUSES,
    SALES_ORIGINS,
    TRANSACTION_STATUSES,
)
from app.transform.settings import (
    DEFAULT_ADJUSTMENT_REVIEW_CENTS,
    DEFAULT_COUNT_REVIEW_CENTS,
    DEFAULT_COUNT_REVIEW_PERCENT,
    UNIT_SYSTEMS,
)
from app.transform.suppliers import ORDERING_METHODS
from app.transform.waste import STATUSES as WASTE_STATUSES
from app.units import BASE_UNIT, Dimension


def utcnow() -> datetime:
    return datetime.now(UTC)


# Where a row may come from. The database refuses anything else (DB1).
COUNT_SOURCES = ("manual", "opening", "receipt", "scan")
IDENTIFIER_SOURCES = ("manual", "square", "scan")
BATCH_SOURCES = ("receipt", "label", "scan", "production")
SUPPRESSION_SOURCES = ("bounce", "complaint", "manual")
# R1 role presets (USER_ROLES_AND_PERMISSIONS §2, PD-11); their permissions arrive in T1.06.
MEMBERSHIP_ROLES = ("owner", "administrator", "manager", "employee", "read_only")
MEMBERSHIP_STATUSES = ("active", "revoked")
AUTH_TOKEN_PURPOSES = ("verify_email", "reset_password")
# Who made an audited change: a member, the product itself (webhooks, jobs), or support (T4.07).
AUDIT_ACTORS = ("user", "system", "support")
# What an inventory item is (DOMAIN_MODEL InventoryItem, C-38).
ITEM_TYPES = ("raw", "prepared", "finished", "packaging", "supply", "resale")
DIMENSIONS = tuple(d.value for d in Dimension)


def ref(target: str) -> ForeignKey:
    """A reference that blocks deleting what it points at: master data is deactivated (DR-24)."""
    return ForeignKey(target, ondelete="RESTRICT")


def earlier(target: str) -> ForeignKey:
    """A reference to an earlier row of the same append-only table (a reversal to what it
    reverses). Checked at the end of each statement (NO ACTION) rather than row by row, so a
    row that is pointed at still cannot be deleted, yet the table can be emptied whole."""
    return ForeignKey(target)


def owned_by(target: str) -> ForeignKey:
    """A part of its parent row, deleted with it (a document's lines, a sold item's recipe)."""
    return ForeignKey(target, ondelete="CASCADE")


def not_negative(table: str, column: str) -> CheckConstraint:
    # Qty is text on SQLite: compare it as a number there, or "-0.0000" would read as negative.
    return CheckConstraint(
        f"CAST({column} AS NUMERIC) >= 0", name=f"ck_{table}_{column}_not_negative"
    )


def positive(table: str, column: str) -> CheckConstraint:
    return CheckConstraint(f"CAST({column} AS NUMERIC) > 0", name=f"ck_{table}_{column}_positive")


# connected → revoked (the provider ended it) or disconnected (the business did) (DOMAIN_MODEL §5).
CONNECTION_STATUSES = ("active", "revoked", "disconnected")
ORDER_STATES = ("open", "completed", "canceled")
WEBHOOK_STATUSES = ("received", "processed", "ignored")


def one_of(table: str, column: str, values: tuple[str, ...]) -> CheckConstraint:
    allowed = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=f"ck_{table}_{column}")


class Plan(str, enum.Enum):
    """What the customer is paying for. TRIAL and CANCELED are states, not plans: they are
    kept only so the stored enum type does not have to be rewritten, and no new row uses them."""

    STARTER = "starter"
    PRO = "pro"
    TRIAL = "trial"  # legacy
    CANCELED = "canceled"  # legacy


class SubscriptionStatus(str, enum.Enum):
    """Where the account stands with us, named the way Stripe names it.

    TRIALING and ACTIVE are the paying states. PAST_DUE keeps working through a grace period
    while the card is retried. EXPIRED, CANCELED and PAUSED are read-only: the inventory stays
    visible and keeps syncing, but nothing can be changed until someone subscribes.
    """

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    EXPIRED = "expired"
    CANCELED = "canceled"
    PAUSED = "paused"


class TrackMode(str, enum.Enum):
    """How a stock item quantity is kept current."""

    PROVIDER = "provider"  # POS is authoritative (countable, POS-tracked SKUs)
    DERIVED = "derived"  # last physical count minus recipe depletion from sales
    MANUAL = "manual"  # set by hand only


class IdentifierType(str, enum.Enum):
    UPC = "upc"
    EAN = "ean"
    SKU = "sku"  # a supplier's or the merchant's own code, not a scanned symbology
    OTHER = "other"


class PromotionStatus(str, enum.Enum):
    ACTIVE = "active"
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"
    ENDED = "ended"


class AlertKind(str, enum.Enum):
    LOW_STOCK = "low_stock"
    OUT_OF_STOCK = "out_of_stock"
    STALE_COUNT = "stale_count"
    WASTE_RISK = "waste_risk"
    EXPIRED = "expired"


class SyncStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.PRO)

    # The subscription, as Stripe sees it and as we enforce it. app/entitlements.py is the only
    # place that decides what these mean.
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus), default=SubscriptionStatus.TRIALING
    )
    trial_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    trial_ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    current_period_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    current_period_end: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # When a payment first failed, which is where the grace period counts from.
    past_due_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(80))
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(80))
    # When the newest Stripe event we applied was created, so an older one arriving late
    # cannot undo a newer state.
    stripe_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The business profile (ADM-FR-01): where it is and who to write to about the account.
    address: Mapped[str | None] = mapped_column(String(300))
    contact_email: Mapped[str | None] = mapped_column(String(255))
    report_day: Mapped[int] = mapped_column(Integer, default=0)  # 0 = Monday
    report_hour: Mapped[int] = mapped_column(Integer, default=7)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # Expiry (T2.08): how many days before a lot's date it is flagged (BAT-FR-09), and whether
    # expired prepared batches are written off at day close (BAT-FR-11; off by default, PD-29).
    expiry_alert_days: Mapped[int] = mapped_column(Integer, default=2)
    auto_dispose_prepared: Mapped[bool] = mapped_column(Boolean, default=False)
    # PD-37: daily sales are entered by Managers and above unless the Owner lets Employees.
    employees_enter_sales: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    users: Mapped[list[User]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set when the person proved the address is theirs; POS, invoice reading and invitations
    # wait for it (SAAS-A-02).
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Two-step sign-in (SAAS-A-07): the authenticator secret, encrypted with the application key;
    # set while enrolling, in force once mfa_enabled_at is set. mfa_last_step stops a code from
    # being used twice.
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(255))
    mfa_enabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    mfa_last_step: Mapped[int | None] = mapped_column(Integer)

    # What this person wants sent. Sign-in links and receipts ignore these: they were asked for.
    wants_stock_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    wants_expiry_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    wants_weekly_report: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class Membership(Base):
    """A person's access to one business: a role, and all of its locations or a list (SAAS-T-03).

    Users are global identities; a user reaches a business only through an active membership.
    users.tenant_id is the business they signed up with, kept until the contract step (DM-16).
    """

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        one_of("memberships", "role", MEMBERSHIP_ROLES),
        one_of("memberships", "status", MEMBERSHIP_STATUSES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    user_id: Mapped[int] = mapped_column(ref("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="active")
    # False limits the member to the locations in membership_locations (STF-FR-10).
    all_locations: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    locations: Mapped[list[MembershipLocation]] = relationship(cascade="all, delete-orphan")


class MembershipLocation(Base):
    """One location a location-scoped member may work at."""

    __tablename__ = "membership_locations"
    __table_args__ = (UniqueConstraint("membership_id", "location_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    membership_id: Mapped[int] = mapped_column(owned_by("memberships.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))


class Invitation(Base):
    """An emailed offer to join a business with a role and a location scope (SAAS-A-08).

    Bound to one email address, single use, seven days. Only the token's hash is stored. Pending
    until accepted or revoked; past expires_at it opens nothing.
    """

    __tablename__ = "invitations"
    __table_args__ = (one_of("invitations", "role", MEMBERSHIP_ROLES),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(24))
    all_locations: Mapped[bool] = mapped_column(Boolean, default=True)
    invited_by_user_id: Mapped[int] = mapped_column(ref("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    locations: Mapped[list[InvitationLocation]] = relationship(cascade="all, delete-orphan")


class InvitationLocation(Base):
    """One location a location-scoped invitation grants."""

    __tablename__ = "invitation_locations"
    __table_args__ = (UniqueConstraint("invitation_id", "location_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    invitation_id: Mapped[int] = mapped_column(owned_by("invitations.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))


class AuthToken(Base):
    """A single-use link emailed to a person: confirm the address, or reset the password.

    Only the token's hash is stored, so a copy of the database opens nothing (SAAS-A-05).
    """

    __tablename__ = "auth_tokens"
    __table_args__ = (one_of("auth_tokens", "purpose", AUTH_TOKEN_PURPOSES),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ref("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(24))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RecoveryCode(Base):
    """A single-use code that stands in for an authenticator code (SAAS-A-07). Hash only."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(owned_by("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AuditEvent(Base):
    """One change to a business's settings, team, connections or plan (DR-23, SEC-14).

    Append-only: written by app/audit.py and never updated or deleted. location_id is set for a
    change at one location; business-wide changes leave it empty.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        one_of("audit_events", "actor_kind", AUDIT_ACTORS),
        Index("ix_audit_events_tenant_at", "tenant_id", "at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    location_id: Mapped[int | None] = mapped_column(ref("locations.id"))
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    actor_kind: Mapped[str] = mapped_column(String(16), default="user")
    actor_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(500))
    # Only the fields that changed, as they were and as they became.
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(45))


class Throttle(Base):
    """Recent attempts at one guarded action by one account or address (SAAS-A-03).

    The key is the rule's name and a hash of the email or IP address, so neither is stored.
    Rows live in the database, so every app server counts the same attempts.
    """

    __tablename__ = "throttles"
    __table_args__ = (not_negative("throttles", "attempts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class UserSession(Base):
    """A signed-in browser (SAAS-A-06). The cookie holds only a random token; this row, found by
    the token's hash, says whose session it is, which business it acts for, and whether it is over.
    """

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ref("users.id"), index=True)
    # The business the person switched to; None until they choose (their first is used).
    membership_id: Mapped[int | None] = mapped_column(ref("memberships.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ProviderConnection(Base):
    """A business's authorization to read one merchant's POS (T3.02, ADR-0076); written only by
    `app/connections.py`. Disconnected and revoked rows stay as history; reconnecting reuses
    the business's row for that merchant."""

    __tablename__ = "provider_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "merchant_id"),
        one_of("provider_connections", "status", CONNECTION_STATUSES),
        CheckConstraint(
            "status <> 'active' OR access_token_enc IS NOT NULL",
            name="ck_provider_connections_token",
        ),
        # One business per merchant across all businesses (DR-22, AC-POS-11): webhooks name the
        # merchant, and Square's revocation ends every token the merchant granted.
        Index(
            "uq_provider_connections_active",
            "provider",
            "merchant_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="square")
    merchant_id: Mapped[str] = mapped_column(String(120))
    # None once disconnected: a connection that has let go of the merchant holds no token.
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExternalLocation(Base):
    """A location the POS reports (T3.03, SQ-05). Which of the business's locations tracks it is
    that location's `provider_location_id`; one tracking none is listed as not tracked."""

    __tablename__ = "external_locations"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_location_id", name="uq_external_locations_external_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    connection_id: Mapped[int] = mapped_column(ref("provider_connections.id"), index=True)
    external_location_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    address: Mapped[str | None] = mapped_column(String(300))
    # False once the POS closes it or stops listing it; kept for history (SQ-08).
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExternalProduct(Base):
    """One sellable variation in the POS's catalog (T3.03, POS-FR-04): the mapping grain. Its
    price and unit are the POS's, informational only (RCP-FR-01, SQ-09)."""

    __tablename__ = "external_products"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_variation_id", name="uq_external_products_variation"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    connection_id: Mapped[int] = mapped_column(ref("provider_connections.id"), index=True)
    # Unknown on a row carried over from sold_items until the next catalog sync (DM-13).
    external_item_id: Mapped[str | None] = mapped_column(String(120))
    external_variation_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(300))
    variation_name: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str | None] = mapped_column(String(200))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    unit: Mapped[str | None] = mapped_column(String(16))
    # False once the POS archives or deletes it; its mapping and history stay (SQ-08).
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExternalModifier(Base):
    """One modifier in the POS's catalog, with the list it belongs to (T3.03, POS-FR-04)."""

    __tablename__ = "external_modifiers"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_modifier_id", name="uq_external_modifiers_external_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    connection_id: Mapped[int] = mapped_column(ref("provider_connections.id"), index=True)
    external_modifier_id: Mapped[str] = mapped_column(String(120))
    list_name: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ProductMapping(Base):
    """What a POS product or modifier is in the business's own catalog (T3.04, ADR-0078): an
    external variation to a product size, an external modifier to a modifier. Written only by
    `app/mappings.py`. A change supersedes the active row, so who mapped what, and when, stays."""

    __tablename__ = "product_mappings"
    __table_args__ = (
        one_of("product_mappings", "status", MAPPING_STATUSES),
        CheckConstraint(
            "(external_product_id IS NOT NULL AND product_variation_id IS NOT NULL"
            " AND external_modifier_id IS NULL AND modifier_id IS NULL)"
            " OR (external_modifier_id IS NOT NULL AND modifier_id IS NOT NULL"
            " AND external_product_id IS NULL AND product_variation_id IS NULL)",
            name="ck_product_mappings_target",
        ),
        CheckConstraint(
            "status <> 'superseded' OR superseded_at IS NOT NULL",
            name="ck_product_mappings_superseded_at",
        ),
        # One active mapping per external id (DOMAIN_MODEL §5).
        Index(
            "uq_product_mappings_active_product",
            "external_product_id",
            unique=True,
            sqlite_where=text("status = 'active' AND external_product_id IS NOT NULL"),
            postgresql_where=text("status = 'active' AND external_product_id IS NOT NULL"),
        ),
        Index(
            "uq_product_mappings_active_modifier",
            "external_modifier_id",
            unique=True,
            sqlite_where=text("status = 'active' AND external_modifier_id IS NOT NULL"),
            postgresql_where=text("status = 'active' AND external_modifier_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    external_product_id: Mapped[int | None] = mapped_column(ref("external_products.id"), index=True)
    external_modifier_id: Mapped[int | None] = mapped_column(
        ref("external_modifiers.id"), index=True
    )
    product_variation_id: Mapped[int | None] = mapped_column(ref("product_variations.id"))
    modifier_id: Mapped[int | None] = mapped_column(ref("modifiers.id"))
    status: Mapped[str] = mapped_column(String(12), default="active")
    # None when the product made it: carried over from sold items (DM-13).
    mapped_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    mapped_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class OrderCursor(Base):
    """How far a connection's orders have been read (T3.05, SQ-11): orders changed up to
    `through` are in. Business dates from `lines_from` on are sold as order lines; earlier days
    keep the daily totals the legacy sync recorded."""

    __tablename__ = "order_cursors"
    __table_args__ = (UniqueConstraint("connection_id", name="uq_order_cursors_connection"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    connection_id: Mapped[int] = mapped_column(ref("provider_connections.id"))
    lines_from: Mapped[date] = mapped_column(Date)
    through: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExternalOrder(Base):
    """A POS order as the POS last told it (T3.05, DOMAIN_MODEL staging): normalized into one
    sale when it is completed at a tracked location. A newer version replaces it; an older or
    repeated one changes nothing."""

    __tablename__ = "external_orders"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_order_id", name="uq_external_orders_external_id"
        ),
        one_of("external_orders", "state", ORDER_STATES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    connection_id: Mapped[int] = mapped_column(ref("provider_connections.id"), index=True)
    external_order_id: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(12))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    external_location_id: Mapped[str] = mapped_column(String(120))
    # The business's location that tracked it when it arrived; None: not tracked, not a sale.
    location_id: Mapped[int | None] = mapped_column(ref("locations.id"))
    # The sale it last became (its latest revision); None while it is not one.
    sales_transaction_id: Mapped[int | None] = mapped_column(ref("sales_transactions.id"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    lines: Mapped[list[ExternalOrderLine]] = relationship(
        back_populates="order", order_by="ExternalOrderLine.id", cascade="all, delete-orphan"
    )


class ExternalOrderLine(Base):
    """A line of a POS order, by its line uid: the idempotency grain (SQ-10)."""

    __tablename__ = "external_order_lines"
    __table_args__ = (UniqueConstraint("order_id", "line_uid", name="uq_external_order_lines_uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    order_id: Mapped[int] = mapped_column(owned_by("external_orders.id"), index=True)
    line_uid: Mapped[str] = mapped_column(String(120))
    # None for a custom amount: kept here, never a sale line (SQ-18).
    external_variation_id: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    modifiers: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [[id, name, qty], ...]
    gross_cents: Mapped[int | None] = mapped_column(Integer)
    net_cents: Mapped[int | None] = mapped_column(Integer)

    order: Mapped[ExternalOrder] = relationship(back_populates="lines")


class Location(Base):
    """A site the business runs, with or without a POS (ADM-FR-02)."""

    __tablename__ = "locations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "provider_location_id"),
        CheckConstraint(
            "business_day_cutoff_hour BETWEEN 0 AND 23",
            name="ck_locations_business_day_cutoff_hour",
        ),
        CheckConstraint("open_days BETWEEN 0 AND 127", name="ck_locations_open_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    # Set when a POS created the location; one added by the owner has neither (mapping: T3.03).
    provider: Mapped[str | None] = mapped_column(String(32))
    provider_location_id: Mapped[str | None] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300))
    # IANA name. "Today", expiry and reports follow this zone (DR-20, AC-ADM-01).
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # The hour a business day starts: before it, it is still the day before (A-08).
    business_day_cutoff_hour: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    # Weekdays the location opens, Monday as bit 0 (app/transform/business_days.py).
    open_days: Mapped[int] = mapped_column(Integer, default=127, server_default=text("127"))
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    # Off because the plan does not cover it, the POS closed it, or it is archived.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The owner archived it: it stays off whatever the plan or the POS says, until restored.
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SoldItem(Base):
    """A thing the POS sells (a Square catalog variation)."""

    __tablename__ = "sold_items"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "provider_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="square")
    provider_item_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(200))
    sku: Mapped[str | None] = mapped_column(String(120))
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    # Square sells some items by measure: deli counter, wine by the glass.
    sell_unit: Mapped[str] = mapped_column(String(16), default="each")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SplitField:
    """A stock item field that now lives on its split row (C-38, ADR-0036).

    Read from the item (or the location's settings) the stock item is linked to; written to both,
    so the legacy column stays filled until DM-16 drops it. Before the first flush links a new
    stock item, the legacy column is all there is. On the class it is the legacy column, so the
    constructor keeps working; product code must not query it (a test enforces that).
    """

    def __init__(self, legacy: str, relation: str, field: str, also=None) -> None:
        self.legacy, self.relation, self.field = legacy, relation, field
        self.also = also or {}

    def __get__(self, obj, owner=None):
        if obj is None:
            return getattr(owner, self.legacy)
        target = getattr(obj, self.relation)
        return getattr(obj, self.legacy) if target is None else getattr(target, self.field)

    def __set__(self, obj, value) -> None:
        setattr(obj, self.legacy, value)
        target = getattr(obj, self.relation)
        if target is None:
            return
        for field, wanted in {
            self.field: value,
            **{k: f(value) for k, f in self.also.items()},
        }.items():
            if getattr(target, field) != wanted:
                setattr(target, field, wanted)


class DimensionValue(TypeDecorator[Dimension]):
    """A Dimension stored as its plain value ("count", "weight", "volume"), checked by name."""

    impl = String(16)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else Dimension(value).value

    def process_result_value(self, value, dialect):
        return None if value is None else Dimension(value)


class InventoryCategory(Base):
    """A business's own grouping of items (INV-FR-03): renamed, merged and archived, never
    deleted while items use it. An archived category stays on its items but leaves the pickers."""

    __tablename__ = "inventory_categories"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    expiry_alert_days: Mapped[int | None] = mapped_column(Integer)  # None: the business's
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class InventoryItem(Base):
    """Something the business stocks, defined once for the whole business (C-38, ID-10).

    Each location that holds it has an ItemLocation. During the transition stock_items stays the
    row the product reads and writes; app/catalog.py keeps these tables in step with it.
    """

    __tablename__ = "inventory_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
        one_of("inventory_items", "item_type", ITEM_TYPES),
        one_of("inventory_items", "dimension", DIMENSIONS),
        one_of("inventory_items", "batch_order", BATCH_ORDERS),
        not_negative("inventory_items", "default_shelf_life_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    slug: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(String(300))
    item_type: Mapped[str] = mapped_column(String(16), default="raw")
    category_id: Mapped[int | None] = mapped_column(ref("inventory_categories.id"), index=True)
    dimension: Mapped[Dimension] = mapped_column(DimensionValue, default=Dimension.COUNT)
    # Quantities are kept in the dimension's base unit; the display unit is how people count it.
    base_unit: Mapped[str] = mapped_column(String(16), default="each")
    display_unit: Mapped[str] = mapped_column(String(16), default="each")
    tracks_batches: Mapped[bool] = mapped_column(Boolean, default=False)
    tracks_expiry: Mapped[bool | None] = mapped_column(Boolean)
    default_shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    # Which batch stock leaves first when no one chooses (BAT-FR-05): soonest expiry, or
    # first received for stock whose dates do not matter.
    batch_order: Mapped[str] = mapped_column(String(4), default="fefo")
    expiry_alert_days: Mapped[int | None] = mapped_column(Integer)  # None: its category's
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    category_ref: Mapped[InventoryCategory | None] = relationship(lazy="joined")
    packages: Mapped[list[ItemPackage]] = relationship(
        order_by="ItemPackage.name", cascade="all, delete-orphan"
    )
    conversions: Mapped[list[ItemUnitConversion]] = relationship(
        order_by="ItemUnitConversion.id", cascade="all, delete-orphan"
    )

    @property
    def category(self) -> str | None:
        """The category's name, as readers have always seen it."""
        return self.category_ref.name if self.category_ref is not None else None

    @category.setter
    def category(self, name: str | None) -> None:
        """Assign by name: an existing category (any case) or a new one (app/catalog.py)."""
        from app.catalog import category_named

        self.category_ref = category_named(object_session(self), self.tenant_id, name)


class ItemPackage(Base):
    """A way an item comes, named by the business: "case" = 6 × gal, "bottle" = 750 ml
    (INV-FR-04, INV-FR-11). The unit is a built-in unit or another of the item's packages. The
    one marked purchase is how deliveries arrive; it sets the stock rows' pack size."""

    __tablename__ = "item_packages"
    __table_args__ = (
        UniqueConstraint("inventory_item_id", "name"),
        positive("item_packages", "quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(owned_by("inventory_items.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit: Mapped[str] = mapped_column(String(60))
    is_purchase: Mapped[bool] = mapped_column(Boolean, default=False)


class ItemUnitConversion(Base):
    """How one item's weight and volume (or count) relate, entered by someone who measured it:
    1 lb of carrots = 340 ml of juice (INV-FR-05). Never assumed from a density table."""

    __tablename__ = "item_unit_conversions"
    __table_args__ = (
        positive("item_unit_conversions", "from_quantity"),
        positive("item_unit_conversions", "to_quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(owned_by("inventory_items.id"), index=True)
    from_quantity: Mapped[Decimal] = mapped_column(Qty)
    from_unit: Mapped[str] = mapped_column(String(16))
    to_quantity: Mapped[Decimal] = mapped_column(Qty)
    to_unit: Mapped[str] = mapped_column(String(16))


class ItemLocation(Base):
    """An inventory item's settings at one location: when to reorder, how much to keep."""

    __tablename__ = "item_locations"
    __table_args__ = (
        UniqueConstraint("location_id", "inventory_item_id"),
        not_negative("item_locations", "reorder_point_base"),
        not_negative("item_locations", "par_level_base"),
        not_negative("item_locations", "min_level_base"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(owned_by("inventory_items.id"), index=True)
    reorder_point_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    par_level_base: Mapped[Decimal | None] = mapped_column(Qty)
    min_level_base: Mapped[Decimal | None] = mapped_column(Qty)
    storage_area: Mapped[str | None] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    inventory_item: Mapped[InventoryItem] = relationship()


class StockItem(Base):
    """A thing the business physically holds, at one location.

    Being split (C-38): its definition moves to InventoryItem and its per-location settings to
    ItemLocation. It stays the compatibility layer, linked to both, until DM-16.
    """

    __tablename__ = "stock_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "location_id", "slug"),
        Index("ix_stock_items_tenant_location", "tenant_id", "location_id"),
        # No check on qty_base: a recorded balance may go negative from sales (DR-08).
        not_negative("stock_items", "reorder_point_base"),
        not_negative("stock_items", "par_level_base"),
        positive("stock_items", "pack_size_base"),
        not_negative("stock_items", "lead_time_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"), index=True)
    slug: Mapped[str] = mapped_column(String(160))
    # Legacy columns (DM-16 drops them); the split fields below are what code reads and writes.
    _name: Mapped[str] = mapped_column("name", String(300))
    _category: Mapped[str | None] = mapped_column("category", String(200))
    _dimension: Mapped[Dimension] = mapped_column(
        "dimension", Enum(Dimension), default=Dimension.COUNT
    )
    _stock_unit: Mapped[str] = mapped_column("stock_unit", String(16), default="each")
    track_mode: Mapped[TrackMode] = mapped_column(Enum(TrackMode), default=TrackMode.PROVIDER)

    qty_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    _reorder_point_base: Mapped[Decimal] = mapped_column(
        "reorder_point_base", Qty, default=Decimal("0")
    )
    _par_level_base: Mapped[Decimal | None] = mapped_column("par_level_base", Qty)
    cost_per_base_cents: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    # What it sells for. Only things sold as they are can be marked down: the milk inside a
    # latte has no shelf price, so it never gets a promotion.
    retail_per_base_cents: Mapped[Decimal | None] = mapped_column(Qty)

    # Set only when the POS itself tracks this SKU count.
    provider_variation_id: Mapped[str | None] = mapped_column(String(120), index=True)
    supplier: Mapped[str | None] = mapped_column(String(200))
    lead_time_days: Mapped[int] = mapped_column(Integer, default=2)

    # Deliveries arrive as packs: one case of 6 x 1L milk is 6000 ml of stock.
    receive_unit: Mapped[str] = mapped_column(String(16), default="each")
    pack_size_base: Mapped[Decimal | None] = mapped_column(Qty)

    # None until someone says. True is perishable, False was dismissed as never expiring,
    # which is what keeps the coverage figure honest rather than counting cups and bags.
    _tracks_expiry: Mapped[bool | None] = mapped_column("tracks_expiry", Boolean)

    last_counted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    _is_active: Mapped[bool] = mapped_column("is_active", Boolean, default=True)

    # The split rows this one stands for; set by app/catalog.py whenever a stock item is saved.
    inventory_item_id: Mapped[int | None] = mapped_column(ref("inventory_items.id"), index=True)
    item_location_id: Mapped[int | None] = mapped_column(ref("item_locations.id"), unique=True)
    inventory_item: Mapped[InventoryItem | None] = relationship(lazy="joined")
    item_location: Mapped[ItemLocation | None] = relationship(lazy="joined")

    # Read from the business-wide item and this location's settings (T1.13-b).
    name = SplitField("_name", "inventory_item", "name")
    category = SplitField("_category", "inventory_item", "category")
    dimension = SplitField(
        "_dimension", "inventory_item", "dimension", also={"base_unit": BASE_UNIT.__getitem__}
    )
    stock_unit = SplitField("_stock_unit", "inventory_item", "display_unit")
    tracks_expiry = SplitField(
        "_tracks_expiry", "inventory_item", "tracks_expiry", also={"tracks_batches": bool}
    )
    reorder_point_base = SplitField("_reorder_point_base", "item_location", "reorder_point_base")
    par_level_base = SplitField("_par_level_base", "item_location", "par_level_base")
    is_active = SplitField("_is_active", "item_location", "is_active")


class Product(Base):
    """Something the business sells, owned by InventoryOS (T2.01, C-36). A POS catalog is mapped
    onto it (T3.03, T3.04), never the reverse. Deactivated, never deleted (DR-24)."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_products_name"),
        one_of("products", "production_mode", PRODUCTION_MODES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    # PRD-FR-11, DR-18: whether a sale uses a prepared item in the recipe or that item's recipe.
    production_mode: Mapped[str] = mapped_column(String(16), default="made_to_order")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    variations: Mapped[list[ProductVariation]] = relationship(
        back_populates="product", order_by="ProductVariation.id"
    )


class ProductVariation(Base):
    """One way a product is sold, a size such as 12 oz, with an informational price (the POS is
    the price authority, RCP-FR-01). Its recipe arrives with T2.02."""

    __tablename__ = "product_variations"
    __table_args__ = (
        UniqueConstraint("product_id", "name", name="uq_product_variations_name"),
        not_negative("product_variations", "price_cents"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    product_id: Mapped[int] = mapped_column(owned_by("products.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The POS sale item it was created from (DM-9), so T2.02 can carry its recipe over and
    # T3.03 can map the POS product to it (DM-13).
    source_sold_item_id: Mapped[int | None] = mapped_column(ref("sold_items.id"), unique=True)

    product: Mapped[Product] = relationship(back_populates="variations")


class Modifier(Base):
    """A choice on a sold product with an inventory effect (RCP-FR-03): none, scale the recipe
    by a factor (e.g. light ice x 1.15), or add its own ingredients (its recipe, T2.02)."""

    __tablename__ = "modifiers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_modifiers_name"),
        one_of("modifiers", "effect", MODIFIER_EFFECTS),
        CheckConstraint(
            "(effect = 'scale' AND scale_factor IS NOT NULL "
            "AND CAST(scale_factor AS NUMERIC) > 0) "
            "OR (effect <> 'scale' AND scale_factor IS NULL)",
            name="ck_modifiers_scale_factor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    group_name: Mapped[str | None] = mapped_column(String(100))  # e.g. "Ice", "Add-ins"
    effect: Mapped[str] = mapped_column(String(8), default="none")
    scale_factor: Mapped[Decimal | None] = mapped_column(Qty)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Recipe(Base):
    """What one owner is made of, over time (T2.02): a product variation, an "add" modifier or
    a prepared item (its production recipe). Its contents live in immutable versions."""

    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint(
            "(CASE WHEN product_variation_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN modifier_id IS NULL THEN 0 ELSE 1 END)"
            " + (CASE WHEN inventory_item_id IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_recipes_one_owner",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    product_variation_id: Mapped[int | None] = mapped_column(
        ref("product_variations.id"), unique=True
    )
    modifier_id: Mapped[int | None] = mapped_column(ref("modifiers.id"), unique=True)
    inventory_item_id: Mapped[int | None] = mapped_column(ref("inventory_items.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    versions: Mapped[list[RecipeVersion]] = relationship(
        back_populates="recipe", order_by="RecipeVersion.version_no"
    )


class RecipeVersion(Base):
    """One saved state of a recipe, effective from a moment (RCP-FR-07, DR-16). Append-only:
    written by app/recipes.py, never updated or deleted; an edit is the next version."""

    __tablename__ = "recipe_versions"
    __table_args__ = (
        UniqueConstraint("recipe_id", "version_no", name="uq_recipe_versions_number"),
        positive("recipe_versions", "version_no"),
        positive("recipe_versions", "yield_base"),
        Index("ix_recipe_versions_effective", "recipe_id", "effective_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    recipe_id: Mapped[int] = mapped_column(owned_by("recipes.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime)
    # What one making yields (RCP-FR-05): servings for a product, an amount for a prepared item.
    yield_quantity: Mapped[Decimal] = mapped_column(Qty, default=Decimal("1"))
    yield_unit: Mapped[str] = mapped_column(String(16), default="serving")
    yield_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("1"))
    instructions: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ref("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    recipe: Mapped[Recipe] = relationship(back_populates="versions")
    lines: Mapped[list[RecipeLine]] = relationship(
        back_populates="version", order_by="RecipeLine.position"
    )


class RecipeLine(Base):
    """An ingredient of a version: an item, the amount as entered and in its base unit (DR-11);
    packaging such as cups and lids is flagged so costs can split it out (RCP-FR-14, -17)."""

    __tablename__ = "recipe_lines"
    __table_args__ = (
        UniqueConstraint("recipe_version_id", "inventory_item_id", name="uq_recipe_lines_item"),
        positive("recipe_lines", "quantity"),
        positive("recipe_lines", "qty_base"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    recipe_version_id: Mapped[int] = mapped_column(owned_by("recipe_versions.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit: Mapped[str] = mapped_column(String(16))
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    is_packaging: Mapped[bool] = mapped_column(Boolean, default=False)

    version: Mapped[RecipeVersion] = relationship(back_populates="lines")
    item: Mapped[InventoryItem] = relationship(lazy="joined")


class InventoryCount(Base):
    """Point-in-time quantity: a physical count or a POS snapshot."""

    __tablename__ = "inventory_counts"
    __table_args__ = (
        Index("ix_counts_item_time", "stock_item_id", "counted_at"),
        # No check on qty_base: a receipt re-anchors a balance that may be negative (DR-08).
        one_of("inventory_counts", "source", COUNT_SOURCES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str | None] = mapped_column(String(500))
    counted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SalesSource(Base):
    """Where a location's sales come from over a run of business days (T2.04, DR-31): a POS
    adapter, entries by hand, a file import, the API or InventoryOS POS. A location has one
    source of record per business date; its operating mode follows from it (POS-FR-18)."""

    __tablename__ = "sales_sources"
    __table_args__ = (
        one_of("sales_sources", "kind", SALES_ORIGINS),
        CheckConstraint(
            "active_to IS NULL OR active_to >= active_from", name="ck_sales_sources_span"
        ),
        Index("ix_sales_sources_location", "location_id", "active_from"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    kind: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(32))  # a POS adapter's, e.g. "square"
    active_from: Mapped[date] = mapped_column(Date)
    active_to: Mapped[date | None] = mapped_column(Date)
    # Whether its sales count for its days. A POS left connected while sales are entered by
    # hand is not: its sales are kept and flagged, never counted (DR-31).
    of_record: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SalesTransaction(Base):
    """One sale, or one day's sales entered as a whole, from one source (T2.04, POS-FR-17,
    DR-30). Its origin is its source's kind and never changes (DR-34). A changed resend is the
    next revision; the one before it is marked corrected. A sale from a source that is not the
    source of record for its location and business date is kept but never counted (DR-31)."""

    __tablename__ = "sales_transactions"
    __table_args__ = (
        UniqueConstraint(
            "sales_source_id", "source_reference", "revision", name="uq_sales_transactions_ref"
        ),
        one_of("sales_transactions", "origin", SALES_ORIGINS),
        one_of("sales_transactions", "status", TRANSACTION_STATUSES),
        positive("sales_transactions", "revision"),
        Index("ix_sales_transactions_day", "location_id", "business_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    sales_source_id: Mapped[int] = mapped_column(ref("sales_sources.id"), index=True)
    origin: Mapped[str] = mapped_column(String(16))
    source_reference: Mapped[str] = mapped_column(String(200))  # order id, entry, file rows
    revision: Mapped[int] = mapped_column(Integer, default=1)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # None: a day's entry
    business_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="completed")
    of_record: Mapped[bool] = mapped_column(Boolean, default=True)
    gross_cents: Mapped[int | None] = mapped_column(Integer)
    discount_cents: Mapped[int | None] = mapped_column(Integer)
    net_cents: Mapped[int | None] = mapped_column(Integer)
    tax_cents: Mapped[int | None] = mapped_column(Integer)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    lines: Mapped[list[SalesTransactionLine]] = relationship(
        back_populates="sales_transaction", order_by="SalesTransactionLine.id"
    )


class SalesTransactionLine(Base):
    """A line of a sale: an owned product variation, or a POS item not mapped yet (kept with
    its reference so it can be mapped and reprocessed), how many, and what consumption did
    with it (T2.05 records the recipe version it used)."""

    __tablename__ = "sales_transaction_lines"
    __table_args__ = (
        UniqueConstraint("sales_transaction_id", "line_key", name="uq_sales_transaction_lines_key"),
        not_negative("sales_transaction_lines", "quantity"),
        one_of("sales_transaction_lines", "consumption_status", CONSUMPTION_STATUSES),
        CheckConstraint(
            "product_variation_id IS NOT NULL OR external_ref IS NOT NULL",
            name="ck_sales_transaction_lines_what",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    sales_transaction_id: Mapped[int] = mapped_column(owned_by("sales_transactions.id"), index=True)
    line_key: Mapped[str] = mapped_column(String(120))
    product_variation_id: Mapped[int | None] = mapped_column(
        ref("product_variations.id"), index=True
    )
    external_ref: Mapped[str | None] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(String(300), default="")
    quantity: Mapped[Decimal] = mapped_column(Qty)
    gross_cents: Mapped[int | None] = mapped_column(Integer)
    net_cents: Mapped[int | None] = mapped_column(Integer)
    consumption_status: Mapped[str] = mapped_column(String(8), default="pending")
    recipe_version_id: Mapped[int | None] = mapped_column(ref("recipe_versions.id"))

    sales_transaction: Mapped[SalesTransaction] = relationship(back_populates="lines")
    modifiers: Mapped[list[SalesLineModifier]] = relationship(
        back_populates="line", order_by="SalesLineModifier.id"
    )


class SalesLineModifier(Base):
    """A modifier chosen on a sold line, and how many times (e.g. two extra shots)."""

    __tablename__ = "sales_line_modifiers"
    __table_args__ = (
        UniqueConstraint(
            "sales_transaction_line_id", "modifier_id", name="uq_sales_line_modifiers_modifier"
        ),
        positive("sales_line_modifiers", "quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    sales_transaction_id: Mapped[int] = mapped_column(owned_by("sales_transactions.id"), index=True)
    sales_transaction_line_id: Mapped[int] = mapped_column(
        owned_by("sales_transaction_lines.id"), index=True
    )
    modifier_id: Mapped[int] = mapped_column(ref("modifiers.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    line: Mapped[SalesTransactionLine] = relationship(back_populates="modifiers")


class SalesReturn(Base):
    """A refund or return from a source (T2.04; refunds and voids arrive with T3.07). It never
    puts stock back by itself (DR-26): a person classifies it as restock or waste (R1.1)."""

    __tablename__ = "sales_returns"
    __table_args__ = (
        UniqueConstraint("sales_source_id", "source_reference", name="uq_sales_returns_ref"),
        one_of("sales_returns", "origin", SALES_ORIGINS),
        one_of("sales_returns", "status", RETURN_STATUSES),
        CheckConstraint(
            "classification IS NULL OR classification IN ('restock', 'waste')",
            name="ck_sales_returns_classification",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    sales_source_id: Mapped[int] = mapped_column(ref("sales_sources.id"), index=True)
    sales_transaction_id: Mapped[int | None] = mapped_column(ref("sales_transactions.id"))
    origin: Mapped[str] = mapped_column(String(16))
    source_reference: Mapped[str] = mapped_column(String(200))
    # The original sale's reference (e.g. "order:<id>"): how a refund that arrived first is
    # linked once its order does (WF-17).
    original_reference: Mapped[str | None] = mapped_column(String(200))
    business_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="recorded")
    classification: Mapped[str | None] = mapped_column(String(8))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    lines: Mapped[list[SalesReturnLine]] = relationship(
        back_populates="sales_return", order_by="SalesReturnLine.id"
    )


class SalesReturnLine(Base):
    """What came back: a line of the original sale where known, and how many."""

    __tablename__ = "sales_return_lines"
    __table_args__ = (positive("sales_return_lines", "quantity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    sales_return_id: Mapped[int] = mapped_column(owned_by("sales_returns.id"), index=True)
    sales_transaction_line_id: Mapped[int | None] = mapped_column(ref("sales_transaction_lines.id"))
    # The sale line's key, kept so a refund that arrived first is matched to it later.
    line_key: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    amount_cents: Mapped[int | None] = mapped_column(Integer)

    sales_return: Mapped[SalesReturn] = relationship(back_populates="lines")


class Alert(Base):
    """Legacy: the five stock alerts before exceptions (DM-8, T1.19). Read-only history;
    nothing writes it any more (ExceptionRecord replaces it)."""

    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_tenant_open", "tenant_id", "resolved_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    kind: Mapped[AlertKind] = mapped_column(Enum(AlertKind))
    message: Mapped[str] = mapped_column(String(500))
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    threshold_base: Mapped[Decimal] = mapped_column(Qty)
    days_of_cover: Mapped[Decimal | None] = mapped_column(Qty)
    # Money at stake, at cost; set for expiry alerts so the digest can lead with the loss.
    value_cents: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class InventoryAdjustment(Base):
    """A deliberate correction with its reason (T1.22; INV-FR-19, WF-10): the source record of
    one `adjustment` transaction, keyed `adjustment:<id>`. Written only by app/adjustments.py."""

    __tablename__ = "inventory_adjustments"
    __table_args__ = (
        one_of("inventory_adjustments", "reason", ADJUSTMENT_REASONS),
        CheckConstraint(
            "CAST(qty_base AS NUMERIC) <> 0", name="ck_inventory_adjustments_qty_base_not_zero"
        ),
        CheckConstraint(
            "reason <> 'other' OR note IS NOT NULL",
            name="ck_inventory_adjustments_other_has_note",
        ),
        Index("ix_inventory_adjustments_item", "tenant_id", "inventory_item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    location_id: Mapped[int] = mapped_column(ref("locations.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"))
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(300))
    # Set only when the person let it take stock below zero, saying why (DR-08).
    override_reason: Mapped[str | None] = mapped_column(String(300))
    actor_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    transaction_id: Mapped[int | None] = mapped_column(ref("inventory_transactions.id"))
    value_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ItemCostHistory(Base):
    """Every change to an item's current cost at a location, effective-dated (T1.24; INV-FR-07,
    DR-19). Append-only: written by app/costs.py, never updated or deleted. The latest entry is
    the current cost, mirrored on the stock row for the screens."""

    __tablename__ = "item_cost_history"
    __table_args__ = (
        one_of("item_cost_history", "source_type", COST_SOURCES),
        not_negative("item_cost_history", "cost_per_base_cents"),
        Index("ix_item_cost_history_pair", "location_id", "inventory_item_id", "effective_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"))
    cost_per_base_cents: Mapped[Decimal] = mapped_column(Qty)
    source_type: Mapped[str] = mapped_column(String(16))
    # The receipt (a document's id) that set it, when one did.
    source_id: Mapped[str | None] = mapped_column(String(80))
    actor_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    effective_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StockCount(Base):
    """A count session at one location (T1.23; REC-FR-01, WF-09): open while lines are counted,
    then posted once, as one `count` transaction keyed `count:<id>`. Written only by
    app/counts.py."""

    __tablename__ = "stock_counts"
    __table_args__ = (
        one_of("stock_counts", "scope", COUNT_SCOPES),
        one_of("stock_counts", "status", COUNT_STATUSES),
        CheckConstraint(
            "status <> 'posted' OR posted_at IS NOT NULL", name="ck_stock_counts_posted_has_time"
        ),
        Index("ix_stock_counts_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    location_id: Mapped[int] = mapped_column(ref("locations.id"), index=True)
    scope: Mapped[str] = mapped_column(String(16))
    # Which category (its id), storage area (its name) or item (its id); empty for a full count.
    scope_ref: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="open")
    note: Mapped[str | None] = mapped_column(String(300))
    started_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    posted_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    transaction_id: Mapped[int | None] = mapped_column(ref("inventory_transactions.id"))

    lines: Mapped[list[StockCountLine]] = relationship(
        back_populates="count", cascade="all, delete-orphan", order_by="StockCountLine.id"
    )


class StockCountLine(Base):
    """What was counted of one item in a session, when, and by whom (an observation, DR-12), with
    what the ledger expected at that instant and the variance posted for the difference."""

    __tablename__ = "stock_count_lines"
    __table_args__ = (
        UniqueConstraint("stock_count_id", "inventory_item_id", name="uq_stock_count_lines_item"),
        not_negative("stock_count_lines", "counted_qty_base"),
        one_of("stock_count_lines", "reason", VARIANCE_REASONS),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    stock_count_id: Mapped[int] = mapped_column(owned_by("stock_counts.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"))
    counted_qty_base: Mapped[Decimal] = mapped_column(Qty)
    # What was typed, for the record: "3 case + 4 each".
    entered: Mapped[str] = mapped_column(String(200))
    counted_at: Mapped[datetime] = mapped_column(UTCDateTime)
    counted_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    # When counted, a preview for the counter; at posting, what the ledger held at counted_at.
    expected_qty_base: Mapped[Decimal | None] = mapped_column(Qty)
    variance_qty_base: Mapped[Decimal | None] = mapped_column(Qty)
    unit_cost: Mapped[Decimal | None] = mapped_column(Qty)
    variance_cents: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(24))
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    movement_id: Mapped[int | None] = mapped_column(ref("inventory_movements.id"))

    count: Mapped[StockCount] = relationship(back_populates="lines")


_OPEN = "status IN ('open', 'acknowledged')"


class ExceptionRecord(Base):
    """Something that needs a person (T1.19; REC-FR-11..16, WF-27, ADR-0043).

    Open at most once per type and subject (the partial unique index); a recurring condition
    updates it. It closes by a fix (the condition clears and the system resolves it) or by a
    dismissal with a reason, never by editing a quantity. Written only by app/exceptions.py and
    the evaluation in app/orchestration/alerts.py. Named ExceptionRecord, not Exception.
    """

    __tablename__ = "exceptions"
    __table_args__ = (
        one_of("exceptions", "type", EXCEPTION_TYPES),
        one_of("exceptions", "severity", SEVERITIES),
        one_of("exceptions", "status", STATUSES),
        one_of("exceptions", "resolution", RESOLUTIONS),
        CheckConstraint("occurrences >= 1", name="ck_exceptions_occurrences_positive"),
        CheckConstraint(
            f"{_OPEN} OR (resolved_at IS NOT NULL AND resolution IS NOT NULL)",
            name="ck_exceptions_closed_has_resolution",
        ),
        CheckConstraint(
            "status <> 'dismissed' OR resolution_note IS NOT NULL",
            name="ck_exceptions_dismissal_has_reason",
        ),
        Index("ix_exceptions_tenant_status", "tenant_id", "status"),
        Index(
            "uq_exceptions_open",
            "tenant_id",
            "type",
            "subject_type",
            "subject_id",
            unique=True,
            sqlite_where=text(_OPEN),
            postgresql_where=text(_OPEN),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    # Where it is; empty for a business-wide one (a connection, a sales source).
    location_id: Mapped[int | None] = mapped_column(ref("locations.id"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))
    subject_type: Mapped[str] = mapped_column(String(30))
    subject_id: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(500))
    # What it may cost, at cost; never shown to a role without cost access (REC-FR-16).
    money_impact_cents: Mapped[int | None] = mapped_column(Integer)
    quantity_base: Mapped[Decimal | None] = mapped_column(Qty)
    days_of_cover: Mapped[Decimal | None] = mapped_column(Qty)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="open")
    detected_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Empty with resolution "system": the condition cleared on its own (REC-FR-14).
    resolved_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    resolution: Mapped[str | None] = mapped_column(String(16))
    resolution_note: Mapped[str | None] = mapped_column(String(500))
    notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Job(Base):
    """One piece of background work for one organization (T1.20; ID-06, REL-07, SCL-03).

    Queued by app/jobs.py, claimed by the scheduler process's worker with FOR UPDATE SKIP
    LOCKED, run at least once, retried with back-off and dead-lettered into an exception after
    its last attempt. It always names its organization (SAAS-T-06).
    """

    __tablename__ = "jobs"
    __table_args__ = (
        one_of("jobs", "kind", JOB_KINDS),
        one_of("jobs", "status", JOB_STATUSES),
        CheckConstraint("attempts >= 0", name="ck_jobs_attempts_not_negative"),
        CheckConstraint("max_attempts >= 1", name="ck_jobs_max_attempts_positive"),
        Index("ix_jobs_due", "status", "run_at"),
        Index("ix_jobs_tenant_status", "tenant_id", "status"),
        # One waiting copy of the same work: a second request to queue it is the first.
        Index(
            "uq_jobs_pending",
            "tenant_id",
            "kind",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status = 'pending' AND dedupe_key IS NOT NULL"),
            postgresql_where=text("status = 'pending' AND dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    locked_by: Mapped[str | None] = mapped_column(String(120))
    # The last failure, for the founder console (T4.07); never shown to the business.
    last_error: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="square")
    # full: the hourly reconciliation. catalog: a catalog change, pulled on its own.
    kind: Mapped[str] = mapped_column(String(16), default="full")
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus), default=SyncStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    stats_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class EmailSuppression(Base):
    """An address we must stop sending to: it bounced, or someone marked us as spam.

    Kept apart from users and customers on purpose - the provider reports an address, and the
    same address may belong to both, or to neither any more.
    """

    __tablename__ = "email_suppressions"
    __table_args__ = (
        UniqueConstraint("email"),
        one_of("email_suppressions", "source", SUPPRESSION_SOURCES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(20), default="bounce")
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="sent")
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DocumentStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    APPLIED = "applied"
    FAILED = "failed"


class LineStatus(str, enum.Enum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    APPLIED = "applied"
    SKIPPED = "skipped"


class Document(Base):
    """A photographed supplier invoice or delivery note: the receiving record."""

    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_tenant_status", "tenant_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"), index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ref("users.id"))
    kind: Mapped[str] = mapped_column(String(32), default="invoice")
    supplier: Mapped[str | None] = mapped_column(String(200))
    invoice_number: Mapped[str | None] = mapped_column(String(120))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    total_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(8), default="USD")

    # Where the photo lives in upload storage, "<tenant>/<sha256><ext>": a key, not a path.
    storage_key: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(80), default="image/jpeg")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.UPLOADED
    )
    extractor: Mapped[str | None] = mapped_column(String(40))
    raw_extract_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    lines: Mapped[list[DocumentLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentLine(Base):
    """One parsed invoice line, and what it was matched to."""

    __tablename__ = "document_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    document_id: Mapped[int] = mapped_column(owned_by("documents.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=0)

    raw_text: Mapped[str] = mapped_column(String(500))
    parsed_name: Mapped[str] = mapped_column(String(300))
    parsed_qty: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    parsed_unit: Mapped[str | None] = mapped_column(String(32))
    parsed_pack_size: Mapped[Decimal | None] = mapped_column(Qty)
    unit_price_cents: Mapped[int | None] = mapped_column(Integer)
    line_total_cents: Mapped[int | None] = mapped_column(Integer)
    expires_on: Mapped[date | None] = mapped_column(Date)

    stock_item_id: Mapped[int | None] = mapped_column(ref("stock_items.id"), index=True)
    match_confidence: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    match_reason: Mapped[str | None] = mapped_column(String(200))
    received_qty_base: Mapped[Decimal | None] = mapped_column(Qty)
    status: Mapped[LineStatus] = mapped_column(Enum(LineStatus), default=LineStatus.UNMATCHED)

    document: Mapped[Document] = relationship(back_populates="lines")


class ProductIdentifier(Base):
    """A code that points at a stock item: a scanned barcode, or a code printed on an invoice.

    One item carries several - the can, the 24-pack, and whatever the supplier calls it - so
    codes live here rather than as columns on the item.
    """

    __tablename__ = "product_identifiers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "value"),
        one_of("product_identifiers", "source", IDENTIFIER_SOURCES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    identifier_type: Mapped[IdentifierType] = mapped_column(
        Enum(IdentifierType), default=IdentifierType.OTHER
    )
    # Stored as text: a leading zero is the difference between two products.
    value: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="manual")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def _signed(values: frozenset[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


class InventoryTransaction(Base):
    """One business event that changed stock: a receipt, a count, a waste entry (T1.16, C-35).

    Append-only. Only app/ledger writes it. Its idempotency key is unique in the business, so a
    retried posting finds the first one instead of posting twice (DR-05).
    """

    __tablename__ = "inventory_transactions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        one_of("inventory_transactions", "kind", tuple(sorted(TRANSACTION_KINDS))),
        Index("ix_inventory_transactions_source", "tenant_id", "source_type", "source_id"),
        # Only a reversal names the transaction it undoes, and it always does (WF-26).
        CheckConstraint(
            "(kind = 'reversal' AND reverses_transaction_id IS NOT NULL)"
            " OR (kind <> 'reversal' AND reverses_transaction_id IS NULL)",
            name="ck_inventory_transactions_reversal_link",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    actor_user_id: Mapped[int | None] = mapped_column(ref("users.id"))  # None: the system
    reason: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reverses_transaction_id: Mapped[int | None] = mapped_column(
        earlier("inventory_transactions.id"), index=True
    )

    movements: Mapped[list[InventoryMovement]] = relationship(
        order_by="InventoryMovement.id", back_populates="transaction"
    )


class InventoryMovement(Base):
    """A signed quantity of one item at one location, in its base unit (DR-01..04, INV-FR-16).

    Append-only: never updated or deleted; a mistake is undone by a reversal (DR-02). The sign
    follows the type, and the database refuses anything else.
    """

    __tablename__ = "inventory_movements"
    __table_args__ = (
        one_of("inventory_movements", "movement_type", tuple(sorted(MOVEMENT_TYPES))),
        one_of("inventory_movements", "allocation_mode", ALLOCATION_MODES),
        CheckConstraint(
            f"(movement_type IN ({_signed(INCREASES)}) AND CAST(qty_base AS NUMERIC) > 0)"
            f" OR (movement_type IN ({_signed(DECREASES)}) AND CAST(qty_base AS NUMERIC) < 0)"
            f" OR (movement_type IN ({_signed(EITHER_WAY)}) AND CAST(qty_base AS NUMERIC) <> 0)",
            name="ck_inventory_movements_sign_by_type",
        ),
        not_negative("inventory_movements", "unit_cost_snapshot"),
        Index(
            "ix_inventory_movements_item_time",
            "tenant_id",
            "location_id",
            "inventory_item_id",
            "occurred_at",
        ),
        Index("ix_inventory_movements_batch", "tenant_id", "batch_id"),
        CheckConstraint(
            "(movement_type = 'reversal' AND reverses_movement_id IS NOT NULL)"
            " OR (movement_type <> 'reversal' AND reverses_movement_id IS NULL)",
            name="ck_inventory_movements_reversal_link",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    transaction_id: Mapped[int] = mapped_column(owned_by("inventory_transactions.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"))
    batch_id: Mapped[int | None] = mapped_column(ref("stock_batches.id"))
    allocation_mode: Mapped[str] = mapped_column(String(10), default="none")
    movement_type: Mapped[str] = mapped_column(String(24))
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    # What one base unit cost when this moved, in cents (DR-19, PD-08).
    unit_cost_snapshot: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    reason: Mapped[str | None] = mapped_column(String(300))
    reverses_movement_id: Mapped[int | None] = mapped_column(earlier("inventory_movements.id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    transaction: Mapped[InventoryTransaction] = relationship(back_populates="movements")


class StockBalance(Base):
    """The recorded quantity of an item at a location: the sum of its movements (DR-03).

    A projection kept in the same database transaction as every posting, and rebuildable from
    the movements at any time. Only app/ledger writes it.
    """

    __tablename__ = "stock_balances"

    location_id: Mapped[int] = mapped_column(ref("locations.id"), primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    qty_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    last_movement_id: Mapped[int | None] = mapped_column(ref("inventory_movements.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class InventoryEvent(Base):
    """Audit trail: who moved what, by how much, and what triggered it.

    Separate from inventory_counts, which anchors the quantity maths. This only explains history.
    """

    __tablename__ = "inventory_events"
    # source is not checked: older rows carry a value written by a module that has left the
    # product (T0.05), and the ledger's transactions replace this table (T1.16).
    __table_args__ = (Index("ix_events_tenant_time", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    action: Mapped[str] = mapped_column(String(32))  # received | counted | adjusted
    source: Mapped[str] = mapped_column(String(32))  # barcode_scan | invoice | manual | square_sync
    delta_base: Mapped[Decimal | None] = mapped_column(Qty)
    qty_after_base: Mapped[Decimal] = mapped_column(Qty)
    detail: Mapped[str | None] = mapped_column(String(500))
    pushed_to_pos: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StockBatch(Base):
    """A dated lot as it arrived. What it still holds is the sum of the ledger movements that
    name it (DR-15, T2.06), never stored; a batch is never deleted (DR-24)."""

    __tablename__ = "stock_batches"
    __table_args__ = (
        Index("ix_batches_item_expiry", "stock_item_id", "expires_on"),
        positive("stock_batches", "received_qty_base"),
        one_of("stock_batches", "source", BATCH_SOURCES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    received_qty_base: Mapped[Decimal] = mapped_column(Qty)
    expires_on: Mapped[date] = mapped_column(Date)
    # What the packaging called it: best_before | use_by | sell_by | expiry | unknown.
    # A use-by date is a legal limit; a best-before is a quality one, and they are sold differently.
    date_type: Mapped[str] = mapped_column(String(20), default="unknown")
    # How sure we are of a date that was read by camera. None when a person typed it.
    date_confidence: Mapped[Decimal | None] = mapped_column(Qty)
    cost_per_base_cents: Mapped[Decimal | None] = mapped_column(Qty)
    # "receipt" raised stock when it arrived; "label" dated stock that was already on the shelf;
    # "scan" was received by scanning its barcode.
    source: Mapped[str] = mapped_column(String(32), default="receipt")
    lot_code: Mapped[str | None] = mapped_column(String(80))  # the supplier's, when known
    document_line_id: Mapped[int | None] = mapped_column(ref("document_lines.id"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Supplier(Base):
    """Someone the business buys from (T2.10, PRC-FR-01). Deactivated, never deleted: its
    deliveries and history stay (AC-PRC-01)."""

    __tablename__ = "suppliers"
    __table_args__ = (
        one_of("suppliers", "ordering_method", ORDERING_METHODS),
        CheckConstraint("delivery_days BETWEEN 0 AND 127", name="ck_suppliers_delivery_days"),
        not_negative("suppliers", "lead_time_days"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    ordering_method: Mapped[str] = mapped_column(String(10), default="email")
    delivery_days: Mapped[int] = mapped_column(Integer, default=0)  # Monday is bit 0
    lead_time_days: Mapped[int] = mapped_column(Integer, default=2)
    minimum_order_cents: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    items: Mapped[list[SupplierItem]] = relationship(
        back_populates="supplier", order_by="SupplierItem.id"
    )


class SupplierItem(Base):
    """An item as one supplier sells it (PRC-FR-02): its code and name, the purchase unit and how
    much of the item it holds, the last price per purchase unit. An item may have several."""

    __tablename__ = "supplier_items"
    __table_args__ = (
        positive("supplier_items", "pack_size_base"),
        not_negative("supplier_items", "last_price_cents"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    supplier_id: Mapped[int] = mapped_column(owned_by("suppliers.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"), index=True)
    code: Mapped[str | None] = mapped_column(String(80))
    name: Mapped[str | None] = mapped_column(String(300))
    purchase_unit: Mapped[str] = mapped_column(String(40), default="each")
    pack_size_base: Mapped[Decimal] = mapped_column(Qty)
    last_price_cents: Mapped[int | None] = mapped_column(Integer)
    last_price_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    preferred: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    supplier: Mapped[Supplier] = relationship(back_populates="items")


class Receiving(Base):
    """A delivery as received (T2.11, RCV-FR-01): from a supplier, by hand or from an invoice
    document. A draft until posted, which happens exactly once (DR-07, AC-RCV-05)."""

    __tablename__ = "receivings"
    __table_args__ = (
        one_of("receivings", "status", RECEIVING_STATUSES),
        UniqueConstraint("document_id", name="uq_receivings_document"),
        Index("ix_receivings_location_time", "location_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    supplier_id: Mapped[int | None] = mapped_column(ref("suppliers.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ref("documents.id"))
    status: Mapped[str] = mapped_column(String(10), default="draft")
    note: Mapped[str | None] = mapped_column(String(300))
    created_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    posted_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))

    lines: Mapped[list[ReceivingLine]] = relationship(
        back_populates="receiving", order_by="ReceivingLine.id"
    )


class ReceivingLine(Base):
    """One line of a delivery: invoiced, arrived and accepted, in the unit it came in; what was
    accepted enters stock through its own receipt transaction (RCV-FR-09). A correction is a new
    line that names the one it corrects (RCV-FR-12)."""

    __tablename__ = "receiving_lines"
    __table_args__ = (
        one_of("receiving_lines", "status", RECEIVING_LINE_STATUSES),
        not_negative("receiving_lines", "arrived_qty"),
        not_negative("receiving_lines", "accepted_qty"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    receiving_id: Mapped[int] = mapped_column(owned_by("receivings.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"))
    supplier_item_id: Mapped[int | None] = mapped_column(ref("supplier_items.id"))
    description: Mapped[str | None] = mapped_column(String(300))
    ordered_qty: Mapped[Decimal | None] = mapped_column(Qty)  # with purchase orders (R1.1)
    invoiced_qty: Mapped[Decimal | None] = mapped_column(Qty)
    arrived_qty: Mapped[Decimal] = mapped_column(Qty)
    accepted_qty: Mapped[Decimal] = mapped_column(Qty)
    unit: Mapped[str] = mapped_column(String(40), default="")
    qty_base: Mapped[Decimal] = mapped_column(Qty)  # accepted, in the item's base unit
    unit_price_cents: Mapped[int | None] = mapped_column(Integer)  # per `unit`
    expires_on: Mapped[date | None] = mapped_column(Date)
    lot_code: Mapped[str | None] = mapped_column(String(80))
    reject_reason: Mapped[str | None] = mapped_column(String(200))
    price_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="open")
    batch_id: Mapped[int | None] = mapped_column(ref("stock_batches.id"))
    transaction_id: Mapped[int | None] = mapped_column(ref("inventory_transactions.id"))
    corrects_line_id: Mapped[int | None] = mapped_column(earlier("receiving_lines.id"))
    correction_reason: Mapped[str | None] = mapped_column(String(300))

    receiving: Mapped[Receiving] = relationship(back_populates="lines")


class ImportMapping(Base):
    """Which columns of a sales export hold the date, product and quantity (T2.13, STD-FR-03),
    saved per business for files with the same header."""

    __tablename__ = "import_mappings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "header_key", name="uq_import_mappings_header"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    header_key: Mapped[str] = mapped_column(String(500))
    date_column: Mapped[str] = mapped_column(String(120))
    product_column: Mapped[str] = mapped_column(String(120))
    quantity_column: Mapped[str] = mapped_column(String(120))
    date_format: Mapped[str] = mapped_column(String(20), default="auto")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ImportName(Base):
    """A product name from a sales file, matched once to a product size and remembered."""

    __tablename__ = "import_names"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_import_names_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))  # normalized
    product_variation_id: Mapped[int] = mapped_column(ref("product_variations.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SalesImport(Base):
    """An uploaded sales file, kept in the business's storage, previewed then posted."""

    __tablename__ = "sales_imports"
    __table_args__ = (one_of("sales_imports", "status", ("preview", "posted")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    filename: Mapped[str] = mapped_column(String(200))
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(500))
    mapping_id: Mapped[int | None] = mapped_column(ref("import_mappings.id"))
    status: Mapped[str] = mapped_column(String(10), default="preview")
    days_posted: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[int | None] = mapped_column(ref("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ProductionOrder(Base):
    """A production run (T2.09, WF-11): a prepared item or finished good made from its recipe in
    effect, times a multiplier. R1 records it completed in one step (PRD-FR-08); its ledger
    transaction takes the inputs and puts the output into a new batch."""

    __tablename__ = "production_orders"
    __table_args__ = (
        one_of("production_orders", "status", PRODUCTION_STATUSES),
        positive("production_orders", "multiplier"),
        positive("production_orders", "actual_yield_base"),
        Index("ix_production_orders_location_time", "location_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"))  # what was made
    recipe_version_id: Mapped[int] = mapped_column(ref("recipe_versions.id"))
    multiplier: Mapped[Decimal] = mapped_column(Qty)
    expected_yield_base: Mapped[Decimal] = mapped_column(Qty)
    actual_yield_base: Mapped[Decimal] = mapped_column(Qty)
    variance_base: Mapped[Decimal] = mapped_column(Qty)  # actual - expected (PRD-FR-04)
    # Per base unit of the output; None when an input had no cost (PRD-FR-06).
    unit_cost: Mapped[Decimal | None] = mapped_column(Qty)
    batch_code: Mapped[str] = mapped_column(String(40))
    output_batch_id: Mapped[int | None] = mapped_column(ref("stock_batches.id"))
    note: Mapped[str | None] = mapped_column(String(300))
    actor_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    transaction_id: Mapped[int | None] = mapped_column(ref("inventory_transactions.id"))
    status: Mapped[str] = mapped_column(String(10), default="completed")
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    voided_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    void_reason: Mapped[str | None] = mapped_column(String(300))


class WasteReason(Base):
    """Why stock was thrown away (WST-FR-02): the defaults and the business's own. Renamed or
    archived, never deleted; each waste event keeps the name it had then, so reports stay
    stable."""

    __tablename__ = "waste_reasons"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_waste_reasons_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WasteEvent(Base):
    """A loss as the business recorded it (T2.07, WST-FR-01): an item, or a sellable product
    expanded to its recipe, how much, why, by whom and when. Its ledger entries are the stock
    effect and the only source of quantities and costs in reports (DR-33)."""

    __tablename__ = "waste_events"
    __table_args__ = (
        one_of("waste_events", "status", WASTE_STATUSES),
        positive("waste_events", "qty_base"),
        CheckConstraint(
            "(inventory_item_id IS NULL) <> (product_variation_id IS NULL)",
            name="ck_waste_events_what",
        ),
        Index("ix_waste_events_location_time", "location_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"))
    reason_id: Mapped[int] = mapped_column(ref("waste_reasons.id"))
    reason_label: Mapped[str] = mapped_column(String(80))  # as it was named then
    inventory_item_id: Mapped[int | None] = mapped_column(ref("inventory_items.id"))
    product_variation_id: Mapped[int | None] = mapped_column(ref("product_variations.id"))
    recipe_version_id: Mapped[int | None] = mapped_column(ref("recipe_versions.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)  # as entered, in `unit`
    unit: Mapped[str] = mapped_column(String(16), default="")
    qty_base: Mapped[Decimal] = mapped_column(Qty)  # the item's base unit, or products wasted
    note: Mapped[str | None] = mapped_column(String(300))
    # WST-FR-11: an optional link to the sale or production it came from.
    related_type: Mapped[str | None] = mapped_column(String(20))
    related_id: Mapped[str | None] = mapped_column(String(80))
    actor_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    transaction_id: Mapped[int | None] = mapped_column(ref("inventory_transactions.id"))
    status: Mapped[str] = mapped_column(String(10), default="recorded")
    voided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    voided_by_user_id: Mapped[int | None] = mapped_column(ref("users.id"))
    void_reason: Mapped[str | None] = mapped_column(String(300))

    lines: Mapped[list[WasteLine]] = relationship(back_populates="event", order_by="WasteLine.id")


class WasteLine(Base):
    """What a waste event takes out of stock: one item, how much, and the batch when someone
    chose it (WST-FR-04). A product's event has a line per ingredient of its recipe. Reports
    never sum these; they read the ledger (DR-33)."""

    __tablename__ = "waste_lines"
    __table_args__ = (positive("waste_lines", "qty_base"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"))
    waste_event_id: Mapped[int] = mapped_column(owned_by("waste_events.id"), index=True)
    inventory_item_id: Mapped[int] = mapped_column(ref("inventory_items.id"))
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    batch_id: Mapped[int | None] = mapped_column(ref("stock_batches.id"))

    event: Mapped[WasteEvent] = relationship(back_populates="lines")


class Promotion(Base):
    """A markdown the owner decided on, and what happened to sales afterwards.

    Only decisions are stored. Suggestions are recomputed from stock every time, so a stale
    row can never recommend something the shelf has already moved past.
    """

    __tablename__ = "promotions"
    __table_args__ = (Index("ix_promotions_tenant_status", "tenant_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    stock_batch_id: Mapped[int | None] = mapped_column(ref("stock_batches.id"))

    status: Mapped[PromotionStatus] = mapped_column(Enum(PromotionStatus))
    urgency: Mapped[str] = mapped_column(String(16), default="none")

    normal_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    suggested_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    accepted_price_cents: Mapped[int | None] = mapped_column(Integer)

    units_at_risk_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    cost_at_risk_cents: Mapped[int] = mapped_column(Integer, default=0)
    required_velocity: Mapped[Decimal | None] = mapped_column(Qty)
    # Sales a day before the markdown, kept so the effect can be measured against it.
    velocity_before: Mapped[Decimal | None] = mapped_column(Qty)

    units_before_base: Mapped[Decimal | None] = mapped_column(Qty)
    units_after_base: Mapped[Decimal | None] = mapped_column(Qty)

    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    snooze_until: Mapped[date | None] = mapped_column(Date)

    # The reasoning as it was shown, so a decision can be revisited on its own terms.
    reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_by: Mapped[int | None] = mapped_column(ref("users.id"))
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class HealthSnapshot(Base):
    """One night's inventory health, kept so the next day's change can be explained.

    The score alone cannot say why it moved, so each dimension's figures are stored with it.
    """

    __tablename__ = "health_snapshots"
    __table_args__ = (UniqueConstraint("tenant_id", "dt"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    dt: Mapped[date] = mapped_column(Date)
    score: Mapped[int | None] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(20))
    # Per-dimension scores and figures, exactly as HealthReport.to_snapshot() produces them.
    detail_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ExtractionCorrection(Base):
    """One thing the reviewer changed about what the reader produced.

    This is how the extraction is judged: not "did it return JSON" but "how much did somebody
    have to fix before receiving the delivery".
    """

    __tablename__ = "extraction_corrections"
    __table_args__ = (Index("ix_extraction_corrections_tenant_field", "tenant_id", "field"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    document_id: Mapped[int] = mapped_column(owned_by("documents.id"), index=True)
    # Null for a document-level field such as the supplier.
    document_line_id: Mapped[int | None] = mapped_column(owned_by("document_lines.id"))
    # quantity | product_match | cost | supplier | invoice_number | expiry
    field: Mapped[str] = mapped_column(String(24))
    ai_value: Mapped[str | None] = mapped_column(String(300))
    corrected_value: Mapped[str | None] = mapped_column(String(300))
    corrected_by: Mapped[int | None] = mapped_column(ref("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StockAlias(Base):
    """Supplier wording learned from a confirmed match, so the next invoice is automatic."""

    __tablename__ = "stock_aliases"
    __table_args__ = (UniqueConstraint("tenant_id", "alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    alias: Mapped[str] = mapped_column(String(300))
    supplier: Mapped[str | None] = mapped_column(String(200))
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class WebhookEvent(Base):
    """A verified provider event, stored once by its id before anything acts on it (T3.06,
    DR-21, DOMAIN_MODEL's SyncEvent): a repeated delivery changes nothing. Written only by
    `app/webhook_events.py`."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "event_id"),
        one_of("webhook_events", "status", WEBHOOK_STATUSES),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[int | None] = mapped_column(ref("tenants.id"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # received → processed, or ignored (an unknown merchant, a type nothing reads).
    status: Mapped[str] = mapped_column(
        String(12), default="received", server_default=text("'received'")
    )
    # The kind of job that acts on it; None when it is acted on as it arrives.
    handled_by: Mapped[str | None] = mapped_column(String(40))
    entity_ids: Mapped[str] = mapped_column(Text, default="[]", server_default=text("'[]'"))
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class BusinessSettings(Base):
    """A business's settings beyond its profile (T2.19; ADM-FR-03, -05, -06): one row, made on
    first read. Profile, schedule and expiry window stay on the tenant row where they began."""

    __tablename__ = "business_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_business_settings_tenant"),
        one_of("business_settings", "unit_system", UNIT_SYSTEMS),
        one_of("business_settings", "default_production_mode", PRODUCTION_MODES),
        CheckConstraint(
            "digest_hour IS NULL OR (digest_hour >= 0 AND digest_hour <= 23)",
            name="ck_business_settings_digest_hour",
        ),
        CheckConstraint(
            "count_review_percent >= 1 AND count_review_percent <= 100",
            name="ck_business_settings_count_review_percent",
        ),
        not_negative("business_settings", "count_review_cents"),
        not_negative("business_settings", "adjustment_review_cents"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(owned_by("tenants.id"))
    unit_system: Mapped[str] = mapped_column(String(8), default="metric")
    default_production_mode: Mapped[str] = mapped_column(String(16), default="made_to_order")
    digest_hour: Mapped[int | None] = mapped_column(Integer)
    count_review_cents: Mapped[int] = mapped_column(Integer, default=DEFAULT_COUNT_REVIEW_CENTS)
    count_review_percent: Mapped[int] = mapped_column(Integer, default=DEFAULT_COUNT_REVIEW_PERCENT)
    adjustment_review_cents: Mapped[int] = mapped_column(
        Integer, default=DEFAULT_ADJUSTMENT_REVIEW_CENTS
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ModifierScale(Base):
    """A scale modifier's factor from an instant on (T2.19, AC-ADM-06): append-only, so a sale
    reads the factor in effect at its own moment and a change applies to later sales only."""

    __tablename__ = "modifier_scales"
    __table_args__ = (positive("modifier_scales", "scale_factor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    modifier_id: Mapped[int] = mapped_column(owned_by("modifiers.id"), index=True)
    scale_factor: Mapped[Decimal] = mapped_column(Qty)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime)
    recorded_by: Mapped[int | None] = mapped_column(ref("users.id"))
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Onboarding(Base):
    """A business's onboarding (T2.20-b; SAAS §5): its step-0 answer and the POS it names, the
    steps it chose to skip, and when its opening count was taken. Made on first read."""

    __tablename__ = "onboarding"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_onboarding_tenant"),
        one_of("onboarding", "payment_answer", tuple(ONBOARDING_ANSWERS)),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(owned_by("tenants.id"))
    payment_answer: Mapped[str | None] = mapped_column(String(20))
    pos_name: Mapped[str | None] = mapped_column(String(80))
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    skipped: Mapped[str] = mapped_column(String(200), default="")
    opening_counted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# Keeps inventory_items and item_locations in step with stock_items while both exist (C-38).
# Imported last, so it registers for every session wherever the models are loaded.
import app.catalog  # noqa: E402, F401
