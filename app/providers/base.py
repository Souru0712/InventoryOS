"""The POS adapter contract (T3.01; POS-FR-15, DR-29, INTEGRATION_REQUIREMENTS §2).

Provider-independent: what any POS adapter returns, and the capabilities it offers. Business
logic consumes only these normalized types, never a provider's payloads (AC-POS-12); a
provider's package under `app/providers/<code>/` translates to and from them. A capability an
adapter cannot provide raises `Unsupported`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


class Unsupported(NotImplementedError):
    """The adapter does not offer this capability (INTEGRATION_REQUIREMENTS §2)."""


class AdapterError(RuntimeError):
    """The provider refused or failed a request; the message is safe to log.

    `revoked` says the provider no longer honours the connection's authorization (revoked or
    expired beyond refresh): the connection is dead until the merchant reconnects (SQ-01).
    """

    def __init__(self, message: str, *, revoked: bool = False):
        super().__init__(message)
        self.revoked = revoked


@dataclass(frozen=True)
class Credentials:
    """What a connection needs to act for one merchant (tokens are stored encrypted)."""

    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    external_merchant_id: str
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizedLocation:
    external_location_id: str
    name: str
    timezone: str
    address: str | None = None
    active: bool = True


@dataclass(frozen=True)
class NormalizedProduct:
    """One sellable variation: the mapping grain (INTEGRATION_REQUIREMENTS §4)."""

    external_item_id: str
    external_variation_id: str
    name: str
    variation_name: str
    category: str | None = None
    active: bool = True
    price_cents: int | None = None


@dataclass(frozen=True)
class NormalizedModifier:
    external_modifier_id: str
    list_name: str
    name: str


@dataclass(frozen=True)
class Catalog:
    products: tuple[NormalizedProduct, ...]
    modifiers: tuple[NormalizedModifier, ...] = ()


@dataclass(frozen=True)
class LineModifier:
    external_modifier_id: str | None
    name: str
    qty: Decimal = Decimal(1)


@dataclass(frozen=True)
class NormalizedOrderLine:
    line_uid: str
    external_variation_id: str | None  # None for a custom amount: never tied to a product
    name: str
    qty: Decimal
    modifiers: tuple[LineModifier, ...] = ()
    note: str | None = None
    gross_cents: int | None = None
    net_cents: int | None = None


@dataclass(frozen=True)
class RefundLine:
    line_uid: str
    qty: Decimal


@dataclass(frozen=True)
class NormalizedRefund:
    external_refund_id: str
    external_order_id: str
    lines: tuple[RefundLine, ...] = ()
    amount_cents: int | None = None
    status: str = "completed"


@dataclass(frozen=True)
class NormalizedOrder:
    """One order as the POS last told it: a new version replaces the one before."""

    external_order_id: str
    version: int
    state: str  # completed | open | canceled
    occurred_at: datetime  # completion time, else creation
    external_location_id: str
    lines: tuple[NormalizedOrderLine, ...] = ()
    refunds: tuple[NormalizedRefund, ...] = ()


@dataclass(frozen=True)
class NormalizedEvent:
    """A verified webhook: a hint that something changed (INT-P-05)."""

    event_id: str
    type: str
    entity_ids: tuple[str, ...] = ()
    occurred_at: datetime | None = None
    external_merchant_id: str | None = None


@dataclass(frozen=True)
class OrderPage:
    orders: tuple[NormalizedOrder, ...]
    cursor: str | None = None  # None: nothing more after this page


@runtime_checkable
class PosAdapter(Protocol):
    """What a POS adapter offers (TARGET_ARCHITECTURE §5)."""

    provider: str

    def authorize_url(self, state: str) -> str: ...

    def exchange_code(self, code: str) -> Credentials: ...

    def refresh(self, credentials: Credentials) -> Credentials: ...

    def revoke(self, credentials: Credentials) -> None: ...

    def list_locations(self, credentials: Credentials) -> list[NormalizedLocation]: ...

    def sync_catalog(self, credentials: Credentials, since: datetime | None = None) -> Catalog: ...

    def fetch_orders(
        self,
        credentials: Credentials,
        location_ids: list[str],
        since: datetime,
        until: datetime,
        cursor: str | None = None,
    ) -> OrderPage: ...

    def parse_webhook(
        self, raw: bytes, headers: Mapping[str, str], url: str
    ) -> NormalizedEvent | None: ...


@dataclass
class Registry:
    """Adapters by provider code; `square` is the first (T3.01)."""

    adapters: dict[str, PosAdapter] = field(default_factory=dict)

    def register(self, adapter: PosAdapter) -> None:
        if not isinstance(adapter, PosAdapter):
            raise TypeError(f"{adapter!r} does not implement the POS adapter contract")
        self.adapters[adapter.provider] = adapter

    def get(self, provider: str) -> PosAdapter:
        try:
            return self.adapters[provider]
        except KeyError as exc:
            raise Unsupported(f"no adapter for {provider!r}") from exc

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self.adapters))
