"""The snack-box module moved to its own repository (C-37, T0.05); nothing of it remains here."""

import re
from pathlib import Path

from app.main import app
from app.providers.square.oauth import SCOPES
from tests.test_async_correctness import api_routes

ROOT = Path(__file__).resolve().parents[1]

# What the module was called in code, templates and settings. Migrations are left out: the one
# that created its tables and the one that dropped them both have to name them.
SNACK_BOX_TRACES = re.compile(
    r"snack|owns_snack_box|BoxOrder|BoxLine|BoxSettings|SnackProfile|CreditTransaction"
    r"|/shop/|/app/boxes|create_payment_link|ORDERS_WRITE|PAYMENTS_(READ|WRITE)",
    re.IGNORECASE,
)


def test_none_of_the_shops_routes_remain():
    """T0.05 left 53 product routes (88 less the shop's 35); later tasks add their own."""
    paths = [route.path for route in api_routes(app.routes)]
    assert len(paths) >= 53, "the walk must reach the routers' routes"
    assert not [p for p in paths if p.startswith(("/shop", "/app/boxes"))]


def test_square_asks_merchants_for_the_five_product_scopes_only():
    assert SCOPES == [
        "MERCHANT_PROFILE_READ",
        "ITEMS_READ",
        "INVENTORY_READ",
        "INVENTORY_WRITE",
        "ORDERS_READ",
    ]


def test_no_snack_box_code_remains():
    files = [
        *ROOT.joinpath("app").rglob("*.py"),
        *ROOT.joinpath("app").rglob("*.html"),
        *ROOT.joinpath("scripts").rglob("*.py"),
    ]
    found = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if SNACK_BOX_TRACES.search(line)
    ]
    assert found == []
