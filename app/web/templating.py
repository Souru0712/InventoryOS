"""Shared Jinja environment and display filters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.entitlements import allows
from app.entitlements import state as entitlement_state
from app.units import (
    UNITS,
    UNITS_BY_DIMENSION,
    Dimension,
    format_amount,
    format_approx,
    format_qty,
    from_base,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class PublicSettings:
    """The only settings a template can reach. The full Settings object carries the Stripe,
    Square and storage secrets; a template must never be able to print them (S11)."""

    app_base_url: str
    support_email: str


def public_settings() -> PublicSettings:
    settings = get_settings()
    return PublicSettings(
        app_base_url=settings.app_base_url,
        support_email=settings.support_email,
    )


templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def money(cents: int | Decimal | None, currency: str = "USD") -> str:
    if cents is None:
        return "-"
    symbol = {"USD": "$", "GBP": "£", "EUR": "€"}.get(currency, "")
    return f"{symbol}{Decimal(cents) / 100:,.2f}"


def qty(qty_base: Decimal | None, unit_code: str) -> str:
    return "-" if qty_base is None else format_qty(qty_base, unit_code)


def approx(qty_base: Decimal | None, unit_code: str) -> str:
    return "-" if qty_base is None else format_approx(qty_base, unit_code)


def amount(qty_base: Decimal | None, unit_code: str) -> str:
    """A quantity inside a sentence; see units.format_amount."""
    return "-" if qty_base is None else format_amount(qty_base, unit_code)


def approx_amount(qty_base: Decimal | None, unit_code: str) -> str:
    return "-" if qty_base is None else format_amount(qty_base, unit_code, approx=True)


def plain(qty_base: Decimal | None, unit_code: str) -> str:
    return "" if qty_base is None else str(from_base(qty_base, unit_code))


templates.env.filters["money"] = money
templates.env.filters["qty"] = qty
templates.env.filters["plain"] = plain
templates.env.filters["approx"] = approx
templates.env.filters["amount"] = amount
templates.env.filters["approx_amount"] = approx_amount
templates.env.globals.update(
    settings=public_settings(),
    # Every signed-in page can say where the account stands, without each route passing it.
    subscription=entitlement_state,
    entitled=allows,
    Dimension=Dimension,
    UNITS=UNITS,
    UNITS_BY_DIMENSION=UNITS_BY_DIMENSION,
)
