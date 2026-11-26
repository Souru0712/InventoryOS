from app.providers.square.normalize import (
    measurement_units,
    to_location,
    to_products,
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
    place = to_location({"id": "L1", "timezone": "Europe/London"})
    assert (place.external_location_id, place.name, place.timezone, place.active) == (
        "L1",
        "L1",
        "Europe/London",
        True,
    )


def test_a_milligram_stays_a_milligram():
    """Audit SQ8: METRIC_MILLIGRAM was read as grams, a 1000x error."""
    unit = {
        "type": "MEASUREMENT_UNIT",
        "id": "mu-mg",
        "measurement_unit_data": {"measurement_unit": {"weight_unit": "METRIC_MILLIGRAM"}},
    }
    assert measurement_units([unit]) == {"mu-mg": ("mg", Dimension.WEIGHT)}


def test_measurement_units_map_to_our_dimensions():
    assert measurement_units(CATALOG) == {"mu-kg": ("kg", Dimension.WEIGHT)}


def test_catalog_flattens_variations_and_keeps_the_category():
    bag, loose = to_products(CATALOG)
    assert (bag.external_variation_id, bag.name, bag.variation_name) == (
        "var1",
        "House Blend",
        "1kg bag",
    )
    assert (bag.category, bag.unit, bag.price_cents) == ("Coffee", "each", 1800)
    assert (loose.external_variation_id, loose.unit, loose.price_cents) == ("var2", "kg", 2200)
