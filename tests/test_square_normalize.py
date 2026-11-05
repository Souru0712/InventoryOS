from datetime import date
from decimal import Decimal

from app.transform.square_normalize import (
    daily_sales_from_orders,
    measurement_units,
    normalize_catalog,
    normalize_counts,
    normalize_locations,
)
from app.units import Dimension

CATALOG = [
    {"type": "CATEGORY", "id": "cat1", "category_data": {"name": "Coffee"}},
    {
        "type": "MEASUREMENT_UNIT",
        "id": "mu-kg",
        "measurement_unit_data": {"measurement_unit": {"weight_unit": "METRIC_KILOGRAM"}},
    },
    {
        "type": "ITEM",
        "id": "item1",
        "item_data": {
            "name": "House Blend",
            "category_id": "cat1",
            "variations": [
                {
                    "id": "var1",
                    "item_variation_data": {
                        "name": "1kg bag",
                        "sku": "HB-1KG",
                        "price_money": {"amount": 1800, "currency": "USD"},
                        "track_inventory": True,
                    },
                },
                {
                    "id": "var2",
                    "item_variation_data": {
                        "name": "Loose",
                        "price_money": {"amount": 2200, "currency": "USD"},
                        "measurement_unit_id": "mu-kg",
                    },
                },
            ],
        },
    },
    {
        "type": "ITEM",
        "id": "gone",
        "is_deleted": True,
        "item_data": {"name": "Old", "variations": []},
    },
]


def test_locations_normalize_with_defaults():
    rows = normalize_locations(
        [{"id": "L1", "name": "Main", "timezone": "Europe/London", "currency": "GBP"}]
    )
    assert rows == [
        {
            "provider_location_id": "L1",
            "name": "Main",
            "timezone": "Europe/London",
            "currency": "GBP",
            "is_active": True,
        }
    ]


def test_measurement_units_map_to_our_dimensions():
    assert measurement_units(CATALOG) == {"mu-kg": ("kg", Dimension.WEIGHT)}


def test_catalog_flattens_variations_and_keeps_the_category():
    rows = normalize_catalog(CATALOG)
    assert len(rows) == 2
    bag, loose = rows
    assert bag["provider_item_id"] == "var1"
    assert bag["name"] == "House Blend - 1kg bag"
    assert bag["category"] == "Coffee"
    assert bag["tracks_inventory"] is True
    assert bag["sell_unit"] == "each"
    assert loose["sell_unit"] == "kg"
    assert loose["tracks_inventory"] is False


def test_counts_keep_only_the_latest_in_stock_reading():
    counts = normalize_counts(
        [
            {
                "catalog_object_id": "var1",
                "location_id": "L1",
                "state": "IN_STOCK",
                "quantity": "5",
                "calculated_at": "2026-09-01T10:00:00Z",
            },
            {
                "catalog_object_id": "var1",
                "location_id": "L1",
                "state": "IN_STOCK",
                "quantity": "9",
                "calculated_at": "2026-09-02T10:00:00Z",
            },
            {
                "catalog_object_id": "var1",
                "location_id": "L1",
                "state": "WASTE",
                "quantity": "2",
                "calculated_at": "2026-09-03T10:00:00Z",
            },
        ]
    )
    assert counts == [
        {
            "provider_item_id": "var1",
            "provider_location_id": "L1",
            "quantity": Decimal("9"),
            "calculated_at": "2026-09-02T10:00:00Z",
        }
    ]


def test_orders_aggregate_per_item_location_and_local_day():
    orders = [
        {
            "location_id": "L1",
            "closed_at": "2026-09-02T23:30:00Z",
            "line_items": [
                {
                    "catalog_object_id": "var1",
                    "quantity": "2",
                    "gross_sales_money": {"amount": 3600},
                    "total_discount_money": {"amount": 200},
                },
                {
                    "catalog_object_id": "var1",
                    "quantity": "1",
                    "gross_sales_money": {"amount": 1800},
                },
                {
                    "quantity": "1",
                    "gross_sales_money": {"amount": 500},
                },  # custom amount, no catalog id
            ],
        }
    ]
    rows = daily_sales_from_orders(orders, {"L1": "UTC"})
    assert len(rows) == 1
    assert rows[0]["dt"] == date(2026, 9, 2)
    assert rows[0]["units"] == Decimal("3")
    assert rows[0]["gross_cents"] == 5400
    assert rows[0]["net_cents"] == 5200


def test_local_timezone_moves_a_late_sale_to_the_right_day():
    orders = [
        {
            "location_id": "L1",
            "closed_at": "2026-09-03T02:30:00Z",
            "line_items": [
                {"catalog_object_id": "var1", "quantity": "1", "gross_sales_money": {"amount": 400}}
            ],
        }
    ]
    rows = daily_sales_from_orders(orders, {"L1": "America/New_York"})
    assert rows[0]["dt"] == date(2026, 9, 2)


def test_unknown_timezone_does_not_lose_the_sale():
    orders = [
        {
            "location_id": "L1",
            "closed_at": "2026-09-03T02:30:00Z",
            "line_items": [
                {"catalog_object_id": "var1", "quantity": "1", "gross_sales_money": {"amount": 400}}
            ],
        }
    ]
    rows = daily_sales_from_orders(orders, {"L1": "Mars/Olympus"})
    assert len(rows) == 1
