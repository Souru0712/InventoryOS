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
from app.transform.ledger import (
    ALLOCATION_MODES,
    DECREASES,
    EITHER_WAY,
    INCREASES,
    MOVEMENT_TYPES,
    TRANSACTION_KINDS,
)
from app.units import BASE_UNIT, Dimension


def utcnow() -> datetime:
    return datetime.now(UTC)


# Where a row may come from. The database refuses anything else (DB1).
COUNT_SOURCES = ("manual", "opening", "receipt", "scan")
IDENTIFIER_SOURCES = ("manual", "square", "scan")
BATCH_SOURCES = ("receipt", "label", "scan")
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
    __tablename__ = "provider_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "merchant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="square")
    merchant_id: Mapped[str] = mapped_column(String(120))
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


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

    components: Mapped[list[RecipeComponent]] = relationship(
        back_populates="sold_item", cascade="all, delete-orphan"
    )


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


class RecipeComponent(Base):
    """How much base-unit stock one sold unit consumes."""

    __tablename__ = "recipe_components"
    __table_args__ = (
        UniqueConstraint("sold_item_id", "stock_item_id"),
        positive("recipe_components", "qty_base_per_sale"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    sold_item_id: Mapped[int] = mapped_column(owned_by("sold_items.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ref("stock_items.id"), index=True)
    qty_base_per_sale: Mapped[Decimal] = mapped_column(Qty)

    sold_item: Mapped[SoldItem] = relationship(back_populates="components")


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


class SalesDaily(Base):
    """Curated per-day sales, one row per sold item / location / day."""

    __tablename__ = "sales_daily"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sold_item_id", "location_id", "dt"),
        Index("ix_sales_tenant_dt", "tenant_id", "dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ref("tenants.id"), index=True)
    sold_item_id: Mapped[int] = mapped_column(ref("sold_items.id"), index=True)
    location_id: Mapped[int] = mapped_column(ref("locations.id"), index=True)
    dt: Mapped[date] = mapped_column(Date)
    units: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    gross_cents: Mapped[int] = mapped_column(Integer, default=0)
    net_cents: Mapped[int] = mapped_column(Integer, default=0)


class Alert(Base):
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
    """A dated lot as it arrived. Remaining quantity is derived, never stored: transform/expiry."""

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
    document_line_id: Mapped[int | None] = mapped_column(ref("document_lines.id"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


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
    """Provider events already acted on, so a retried delivery changes nothing."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[int | None] = mapped_column(ref("tenants.id"))
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# Keeps inventory_items and item_locations in step with stock_items while both exist (C-38).
# Imported last, so it registers for every session wherever the models are loaded.
import app.catalog  # noqa: E402, F401
