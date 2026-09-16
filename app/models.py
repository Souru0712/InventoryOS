"""Multi-tenant schema. Every business row carries tenant_id."""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Qty
from app.units import Dimension


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Plan(str, enum.Enum):
    TRIAL = "trial"
    STARTER = "starter"
    PRO = "pro"
    CANCELED = "canceled"


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
    plan: Mapped[Plan] = mapped_column(Enum(Plan), default=Plan.TRIAL)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(80))
    report_day: Mapped[int] = mapped_column(Integer, default=0)  # 0 = Monday
    report_hour: Mapped[int] = mapped_column(Integer, default=7)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    users: Mapped[list[User]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    # What this person wants sent. Sign-in links and receipts ignore these: they were asked for.
    wants_stock_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    wants_expiry_alerts: Mapped[bool] = mapped_column(Boolean, default=True)
    wants_weekly_report: Mapped[bool] = mapped_column(Boolean, default=True)

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class ProviderConnection(Base):
    __tablename__ = "provider_connections"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "merchant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="square")
    merchant_id: Mapped[str] = mapped_column(String(120))
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "provider_location_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="square")
    provider_location_id: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SoldItem(Base):
    """A thing the POS sells (a Square catalog variation)."""

    __tablename__ = "sold_items"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "provider_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
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
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
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

    last_counted_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RecipeComponent(Base):
    """How much base-unit stock one sold unit consumes."""

    __tablename__ = "recipe_components"
    __table_args__ = (UniqueConstraint("sold_item_id", "stock_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    sold_item_id: Mapped[int] = mapped_column(ForeignKey("sold_items.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    qty_base_per_sale: Mapped[Decimal] = mapped_column(Qty)

    sold_item: Mapped[SoldItem] = relationship(back_populates="components")


class InventoryCount(Base):
    """Point-in-time quantity: a physical count or a POS snapshot."""

    __tablename__ = "inventory_counts"
    __table_args__ = (Index("ix_counts_item_time", "stock_item_id", "counted_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str | None] = mapped_column(String(500))
    counted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SalesDaily(Base):
    """Curated per-day sales, one row per sold item / location / day."""

    __tablename__ = "sales_daily"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sold_item_id", "location_id", "dt"),
        Index("ix_sales_tenant_dt", "tenant_id", "dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    sold_item_id: Mapped[int] = mapped_column(ForeignKey("sold_items.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    dt: Mapped[date] = mapped_column(Date)
    units: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    gross_cents: Mapped[int] = mapped_column(Integer, default=0)
    net_cents: Mapped[int] = mapped_column(Integer, default=0)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_tenant_open", "tenant_id", "resolved_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    kind: Mapped[AlertKind] = mapped_column(Enum(AlertKind))
    message: Mapped[str] = mapped_column(String(500))
    qty_base: Mapped[Decimal] = mapped_column(Qty)
    threshold_base: Mapped[Decimal] = mapped_column(Qty)
    days_of_cover: Mapped[Decimal | None] = mapped_column(Qty)
    # Money at stake, at cost; set for expiry alerts so the digest can lead with the loss.
    value_cents: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32), default="square")
    # full: the hourly reconciliation. catalog: a catalog change, pulled on its own.
    kind: Mapped[str] = mapped_column(String(16), default="full")
    status: Mapped[SyncStatus] = mapped_column(Enum(SyncStatus), default=SyncStatus.RUNNING)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    stats_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class EmailSuppression(Base):
    """An address we must stop sending to: it bounced, or someone marked us as spam.

    Kept apart from users and customers on purpose - the provider reports an address, and the
    same address may belong to both, or to neither any more.
    """

    __tablename__ = "email_suppressions"
    __table_args__ = (UniqueConstraint("email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    # bounce | complaint | manual
    source: Mapped[str] = mapped_column(String(20), default="bounce")
    reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EmailLog(Base):
    __tablename__ = "email_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    to_email: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="sent")
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


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
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
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

    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus), default=DocumentStatus.UPLOADED)
    extractor: Mapped[str | None] = mapped_column(String(40))
    raw_extract_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)

    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime)

    lines: Mapped[list[DocumentLine]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentLine(Base):
    """One parsed invoice line, and what it was matched to."""

    __tablename__ = "document_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=0)

    raw_text: Mapped[str] = mapped_column(String(500))
    parsed_name: Mapped[str] = mapped_column(String(300))
    parsed_qty: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    parsed_unit: Mapped[str | None] = mapped_column(String(32))
    parsed_pack_size: Mapped[Decimal | None] = mapped_column(Qty)
    unit_price_cents: Mapped[int | None] = mapped_column(Integer)
    line_total_cents: Mapped[int | None] = mapped_column(Integer)
    expires_on: Mapped[date | None] = mapped_column(Date)

    stock_item_id: Mapped[int | None] = mapped_column(ForeignKey("stock_items.id"), index=True)
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
    __table_args__ = (UniqueConstraint("tenant_id", "value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    identifier_type: Mapped[IdentifierType] = mapped_column(
        Enum(IdentifierType), default=IdentifierType.OTHER
    )
    # Stored as text: a leading zero is the difference between two products.
    value: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual | square | scan
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InventoryEvent(Base):
    """Audit trail: who moved what, by how much, and what triggered it.

    Separate from inventory_counts, which anchors the quantity maths. This only explains history.
    """

    __tablename__ = "inventory_events"
    __table_args__ = (Index("ix_events_tenant_time", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(32))  # received | counted | adjusted
    source: Mapped[str] = mapped_column(String(32))  # barcode_scan | invoice | manual | square_sync
    delta_base: Mapped[Decimal | None] = mapped_column(Qty)
    qty_after_base: Mapped[Decimal] = mapped_column(Qty)
    detail: Mapped[str | None] = mapped_column(String(500))
    pushed_to_pos: Mapped[bool] = mapped_column(Boolean, default=False)
    # The paid box that moved this stock. A plain id: the event outlives nothing it points at.
    box_order_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StockBatch(Base):
    """A dated lot as it arrived. Remaining quantity is derived, never stored: transform/expiry."""

    __tablename__ = "stock_batches"
    __table_args__ = (Index("ix_batches_item_expiry", "stock_item_id", "expires_on"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    received_qty_base: Mapped[Decimal] = mapped_column(Qty)
    expires_on: Mapped[date] = mapped_column(Date)
    # What the packaging called it: best_before | use_by | sell_by | expiry | unknown.
    # A use-by date is a legal limit; a best-before is a quality one, and they are sold differently.
    date_type: Mapped[str] = mapped_column(String(20), default="unknown")
    # How sure we are of a date that was read by camera. None when a person typed it.
    date_confidence: Mapped[Decimal | None] = mapped_column(Qty)
    cost_per_base_cents: Mapped[Decimal | None] = mapped_column(Qty)
    # "receipt" raised stock when it arrived; "label" dated stock that was already on the shelf.
    source: Mapped[str] = mapped_column(String(32), default="receipt")
    document_line_id: Mapped[int | None] = mapped_column(ForeignKey("document_lines.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)



class Promotion(Base):
    """A markdown the owner decided on, and what happened to sales afterwards.

    Only decisions are stored. Suggestions are recomputed from stock every time, so a stale
    row can never recommend something the shelf has already moved past.
    """

    __tablename__ = "promotions"
    __table_args__ = (Index("ix_promotions_tenant_status", "tenant_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    stock_batch_id: Mapped[int | None] = mapped_column(ForeignKey("stock_batches.id"))

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

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)


class HealthSnapshot(Base):
    """One night's inventory health, kept so the next day's change can be explained.

    The score alone cannot say why it moved, so each dimension's figures are stored with it.
    """

    __tablename__ = "health_snapshots"
    __table_args__ = (UniqueConstraint("tenant_id", "dt"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    dt: Mapped[date] = mapped_column(Date)
    score: Mapped[int | None] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(20))
    # Per-dimension scores and figures, exactly as HealthReport.to_snapshot() produces them.
    detail_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class StockAlias(Base):
    """Supplier wording learned from a confirmed match, so the next invoice is automatic."""

    __tablename__ = "stock_aliases"
    __table_args__ = (UniqueConstraint("tenant_id", "alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    alias: Mapped[str] = mapped_column(String(300))
    supplier: Mapped[str | None] = mapped_column(String(200))
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# --- snack boxes: shoppers, paid boxes, referrals and store credit ---------------------------------


class BoxOrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    PACKED = "packed"
    PICKED_UP = "picked_up"
    CANCELLED = "cancelled"  # never paid
    REFUNDED = "refunded"  # paid, and the money went back


class BoxLineStatus(str, enum.Enum):
    ALLOCATED = "allocated"
    PACKED = "packed"
    SHORT = "short"  # the till sold it after allocation; needs a replacement before packing
    RELEASED = "released"


class ReferralStatus(str, enum.Enum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    REWARDED = "rewarded"
    CANCELLED = "cancelled"
    REVERSED = "reversed"


class CreditKind(str, enum.Enum):
    REFERRAL_REWARD = "referral_reward"
    REFERRAL_REVERSAL = "referral_reversal"
    REDEMPTION = "redemption"
    REDEMPTION_RETURN = "redemption_return"
    ADJUSTMENT = "adjustment"


class ProductPreferenceState(str, enum.Enum):
    FAVORITE = "favorite"
    NEVER_AGAIN = "never_again"


class BoxSettings(Base):
    """One store's snack box terms, editable without a deploy. Money in cents."""

    __tablename__ = "box_settings"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int | None] = mapped_column(ForeignKey("locations.id"))
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)
    price_sweet_cents: Mapped[int] = mapped_column(Integer, default=2499)
    price_chaos_cents: Mapped[int] = mapped_column(Integer, default=2499)
    price_world_cents: Mapped[int] = mapped_column(Integer, default=2499)
    min_items: Mapped[int] = mapped_column(Integer, default=8)
    max_items: Mapped[int] = mapped_column(Integer, default=12)
    target_cogs_cents: Mapped[int] = mapped_column(Integer, default=800)
    packaging_cents: Mapped[int] = mapped_column(Integer, default=125)
    payment_fee_bps: Mapped[int] = mapped_column(Integer, default=290)
    payment_fee_fixed_cents: Mapped[int] = mapped_column(Integer, default=30)
    min_contribution_cents: Mapped[int] = mapped_column(Integer, default=1000)
    min_days_after_pickup: Mapped[int] = mapped_column(Integer, default=14)
    pickup_after_days: Mapped[int] = mapped_column(Integer, default=2)
    referral_reward_cents: Mapped[int] = mapped_column(Integer, default=500)
    referral_discount_cents: Mapped[int] = mapped_column(Integer, default=500)
    payment_window_minutes: Mapped[int] = mapped_column(Integer, default=60)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SnackProfile(Base):
    """What the box builder needs to know about a stock item that the POS does not."""

    __tablename__ = "snack_profiles"
    __table_args__ = (UniqueConstraint("stock_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"))
    boxable: Mapped[bool] = mapped_column(Boolean, default=False)
    flavors: Mapped[str] = mapped_column(String(120), default="")  # "sweet;spicy"
    origin: Mapped[str] = mapped_column(String(40), default="")
    # None until someone has read the label; an unknown list never goes in a box. "" means none.
    allergens: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Customer(Base):
    """A shopper. Kept apart from users, who run the store: a different login and no store powers."""

    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email"),
        UniqueConstraint("tenant_id", "referral_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(40))
    referral_code: Mapped[str] = mapped_column(String(24))
    # A safety answer: None means never answered, which blocks ordering. "" means "none".
    allergens: Mapped[str | None] = mapped_column(String(200))
    allergens_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    dietary: Mapped[str] = mapped_column(String(200), default="")
    spice: Mapped[str] = mapped_column(String(16), default="medium")
    novelty: Mapped[str] = mapped_column(String(16), default="balanced")
    liked_categories: Mapped[str] = mapped_column(String(500), default="")
    avoided_categories: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CustomerLoginToken(Base):
    """A one-time sign-in link. Only its hash is kept, so a leaked table signs nobody in."""

    __tablename__ = "customer_login_tokens"
    __table_args__ = (UniqueConstraint("token_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CustomerProductPreference(Base):
    __tablename__ = "customer_product_preferences"
    __table_args__ = (UniqueConstraint("customer_id", "stock_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"))
    state: Mapped[ProductPreferenceState] = mapped_column(Enum(ProductPreferenceState))
    source: Mapped[str] = mapped_column(String(16), default="customer")  # customer | survey | packer
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PreferenceChange(Base):
    """Every change to what a shopper told us, so a surprising box can be traced back to its cause."""

    __tablename__ = "preference_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(String(500))
    new_value: Mapped[str | None] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(16), default="customer")
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BoxOrder(Base):
    __tablename__ = "box_orders"
    __table_args__ = (
        Index("ix_box_orders_tenant_status", "tenant_id", "status"),
        UniqueConstraint("tenant_id", "square_order_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    box_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[BoxOrderStatus] = mapped_column(Enum(BoxOrderStatus), default=BoxOrderStatus.PENDING_PAYMENT)
    price_cents: Mapped[int] = mapped_column(Integer)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)  # referral discount on a first box
    credit_cents: Mapped[int] = mapped_column(Integer, default=0)  # store credit applied
    payment_source: Mapped[str | None] = mapped_column(String(16))  # square | counter | credit
    square_order_id: Mapped[str | None] = mapped_column(String(120))
    square_payment_id: Mapped[str | None] = mapped_column(String(120))
    payment_url: Mapped[str | None] = mapped_column(String(500))
    pay_by: Mapped[datetime | None] = mapped_column(DateTime)
    pickup_on: Mapped[date | None] = mapped_column(Date)
    # Why a person has to look at it before it can move on. None when nothing is wrong.
    attention: Mapped[str | None] = mapped_column(String(500))
    rules: Mapped[str | None] = mapped_column(String(16))
    overall_rating: Mapped[int | None] = mapped_column(Integer)  # 1-5 from the survey
    would_reorder: Mapped[str | None] = mapped_column(String(8))  # yes | maybe | no
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    packed_at: Mapped[datetime | None] = mapped_column(DateTime)
    picked_up_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime)

    @property
    def amount_due_cents(self) -> int:
        return max(0, self.price_cents - self.discount_cents - self.credit_cents)


class BoxLine(Base):
    __tablename__ = "box_lines"
    __table_args__ = (UniqueConstraint("box_order_id", "stock_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    box_order_id: Mapped[int] = mapped_column(ForeignKey("box_orders.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"), index=True)
    qty_base: Mapped[Decimal] = mapped_column(Qty, default=Decimal("1"))
    status: Mapped[BoxLineStatus] = mapped_column(Enum(BoxLineStatus), default=BoxLineStatus.ALLOCATED)
    unit_cost_cents: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    # True once these units are out of the count the till sells from (Square's, or ours).
    count_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    fit: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    shelf: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    driver: Mapped[str] = mapped_column(String(16), default="customer")
    why: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)


class BoxReview(Base):
    __tablename__ = "box_reviews"
    __table_args__ = (UniqueConstraint("box_order_id", "stock_item_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    box_order_id: Mapped[int] = mapped_column(ForeignKey("box_orders.id"), index=True)
    stock_item_id: Mapped[int] = mapped_column(ForeignKey("stock_items.id"))
    rating: Mapped[str] = mapped_column(String(16))  # loved | liked | neutral | disliked | never_again
    send_again: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(String(500))
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Referral(Base):
    __tablename__ = "referrals"
    __table_args__ = (UniqueConstraint("tenant_id", "referred_customer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    referrer_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    referred_customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    code: Mapped[str] = mapped_column(String(24))
    status: Mapped[ReferralStatus] = mapped_column(Enum(ReferralStatus), default=ReferralStatus.PENDING)
    box_order_id: Mapped[int | None] = mapped_column(ForeignKey("box_orders.id"))
    # Fixed when the code is used, so changing the reward later does not change what was promised.
    reward_cents: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime)
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime)


class CreditTransaction(Base):
    """Store-credit ledger. A balance is the sum of these; no row is ever edited."""

    __tablename__ = "credit_transactions"
    __table_args__ = (UniqueConstraint("tenant_id", "reference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)  # + earned or returned, - spent or reversed
    kind: Mapped[CreditKind] = mapped_column(Enum(CreditKind))
    # The business event this posts, e.g. referral_reward:12. It can only ever post once.
    reference: Mapped[str] = mapped_column(String(80))
    box_order_id: Mapped[int | None] = mapped_column(ForeignKey("box_orders.id"))
    referral_id: Mapped[int | None] = mapped_column(ForeignKey("referrals.id"))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WebhookEvent(Base):
    """Provider events already acted on, so a retried delivery changes nothing."""

    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80))
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
