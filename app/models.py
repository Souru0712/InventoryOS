"""Multi-tenant schema. Every business row carries tenant_id."""

from __future__ import annotations

import enum
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Qty, UTCDateTime
from app.units import Dimension


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


def ref(target: str) -> ForeignKey:
    """A reference that blocks deleting what it points at: master data is deactivated (DR-24)."""
    return ForeignKey(target, ondelete="RESTRICT")


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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

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


class StockItem(Base):
    """A thing the business physically holds, at one location."""

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
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(200))
    dimension: Mapped[Dimension] = mapped_column(Enum(Dimension), default=Dimension.COUNT)
    stock_unit: Mapped[str] = mapped_column(String(16), default="each")
    track_mode: Mapped[TrackMode] = mapped_column(Enum(TrackMode), default=TrackMode.PROVIDER)

    qty_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    reorder_point_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    par_level_base: Mapped[Decimal | None] = mapped_column(Qty)
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
    tracks_expiry: Mapped[bool | None] = mapped_column(Boolean)

    last_counted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


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
