from decimal import Decimal

from app.transform.receiving import (
    Candidate,
    ParsedLine,
    build_alias_index,
    match_line,
    normalize_name,
    parse_pack,
    parse_size,
    resolve_received_base,
    similarity,
)
from app.units import Dimension, to_base

MILK = Candidate(
    1, "Whole milk", Dimension.VOLUME, "l", receive_unit="l", pack_size_base=to_base(6, "l")
)
FLOUR = Candidate(
    2, "Pastry flour", Dimension.WEIGHT, "kg", receive_unit="kg", pack_size_base=to_base(25, "kg")
)
CUPS = Candidate(
    3,
    "Takeaway cups 12oz",
    Dimension.COUNT,
    "each",
    receive_unit="each",
    pack_size_base=Decimal("500"),
)
BEANS = Candidate(4, "Espresso beans (hopper stock)", Dimension.WEIGHT, "kg", receive_unit="kg")


def line(**overrides) -> ParsedLine:
    defaults = dict(line_no=1, raw_text="", name="", qty=Decimal("1"), unit=None)
    return ParsedLine(**{**defaults, **overrides})


def test_pack_notation_is_read_off_the_line():
    assert parse_pack("Whole Milk 6 x 1L") == (Decimal("6"), Decimal("1"), "l")
    assert parse_pack("12x500g Flour") == (Decimal("12"), Decimal("500"), "g")
    assert parse_pack("Napkins bundle") is None
    assert parse_size("Pastry flour 25kg sack") == (Decimal("25"), "kg")


def test_names_normalize_past_packaging_noise():
    assert normalize_name("Organic Whole Milk 6 x 1L case") == "whole milk"
    assert normalize_name("PASTRY FLOUR 25kg sack") == "pastry flour"
    assert similarity("Whole milk", "Milk, whole") > Decimal("0.6")


def test_volume_case_resolves_to_millilitres():
    received, problem = resolve_received_base(
        line(raw_text="Whole Milk 6 x 1L", name="Whole Milk", qty=Decimal("4"), unit="case"), MILK
    )
    assert problem is None
    assert received == to_base(24, "l")  # 4 cases x 6 x 1L


def test_weight_line_in_kilos_resolves_directly():
    received, problem = resolve_received_base(
        line(raw_text="Pastry flour", name="Pastry flour", qty=Decimal("50"), unit="kg"), FLOUR
    )
    assert problem is None
    assert received == to_base(50, "kg")


def test_plain_case_count_uses_the_items_pack_size():
    received, problem = resolve_received_base(
        line(raw_text="Pastry flour sack", name="Pastry flour", qty=Decimal("2"), unit="sack"),
        FLOUR,
    )
    assert problem is None
    assert received == to_base(50, "kg")


def test_countable_items_receive_as_units():
    received, problem = resolve_received_base(
        line(raw_text="Takeaway cups 12oz", name="Takeaway cups", qty=Decimal("3"), unit="box"),
        CUPS,
    )
    assert problem is None
    assert received == Decimal("1500.0000")  # 3 boxes of 500


def test_unit_mismatch_is_flagged_not_guessed():
    received, problem = resolve_received_base(
        line(raw_text="Espresso beans 5L", name="Espresso beans", qty=Decimal("2"), unit="l"), BEANS
    )
    assert received is None
    assert "volume" in problem and "weight" in problem


def test_missing_pack_size_on_measured_stock_asks_for_help():
    received, problem = resolve_received_base(
        line(raw_text="Espresso beans", name="Espresso beans", qty=Decimal("2"), unit="case"), BEANS
    )
    assert received is None
    assert "pack size" in problem


def test_zero_quantity_is_rejected():
    received, problem = resolve_received_base(
        line(name="Whole Milk", qty=Decimal("0"), unit="l"), MILK
    )
    assert received is None and "zero" in problem


def test_matching_prefers_a_learned_alias():
    aliases = build_alias_index([("NORTHSIDE FULL CREAM MLK", 1)])
    result = match_line(
        line(
            raw_text="NORTHSIDE FULL CREAM MLK 6x1L",
            name="NORTHSIDE FULL CREAM MLK",
            qty=Decimal("2"),
            unit="case",
        ),
        [MILK, FLOUR, CUPS],
        aliases,
    )
    assert result.stock_item_id == 1
    assert result.confidence == Decimal("1")
    assert result.is_auto


def test_matching_falls_back_to_name_similarity():
    result = match_line(
        line(raw_text="Pastry Flour 25kg", name="Pastry Flour", qty=Decimal("1"), unit="sack"),
        [MILK, FLOUR, CUPS],
    )
    assert result.stock_item_id == 2
    assert result.received_qty_base == to_base(25, "kg")


def test_unknown_products_are_not_forced_into_a_match():
    result = match_line(
        line(raw_text="Dishwasher tablets", name="Dishwasher tablets", qty=Decimal("1")),
        [MILK, FLOUR, CUPS],
    )
    assert result.stock_item_id is None
    assert not result.is_auto
    assert result.problem


def test_a_flagged_problem_blocks_auto_apply():
    result = match_line(
        line(
            raw_text="Espresso beans 5L",
            name="Espresso beans (hopper stock)",
            qty=Decimal("2"),
            unit="l",
        ),
        [BEANS],
    )
    assert result.stock_item_id == 4
    assert not result.is_auto  # matched, but the unit does not make sense


# --- product codes -----------------------------------------------------------------------

COLA_12 = Candidate(
    10,
    "Coca-Cola 12oz",
    Dimension.COUNT,
    "each",
    codes=(("UPC", "049000028911"), ("SKU", "123456")),
)
COLA_20 = Candidate(11, "Coca-Cola 20oz", Dimension.COUNT, "each", codes=(("SKU", "654321"),))


def test_a_printed_sku_matches_outright():
    result = match_line(
        line(raw_text="COKE 12OZ 24PK SKU 123456", name="Coke 12oz", qty=Decimal("10")),
        [COLA_20, COLA_12],
    )
    assert result.stock_item_id == 10
    assert result.reason == "SKU 123456"


def test_a_printed_upc_matches_outright():
    result = match_line(line(raw_text="049000028911 COKE", name="Coke"), [COLA_20, COLA_12])
    assert result.stock_item_id == 10
    assert result.reason.startswith("UPC")


def test_a_code_beats_learned_wording_that_sizes_have_collapsed():
    # Sizes are stripped when names are normalized, so both colas learned the same alias.
    aliases = build_alias_index([("Coca-Cola 20oz", 11)])
    result = match_line(
        line(raw_text="COCA-COLA 12OZ SKU 123456", name="Coca-Cola 12oz"),
        [COLA_12, COLA_20],
        aliases,
    )
    assert result.stock_item_id == 10


def test_a_code_inside_a_longer_number_does_not_match():
    result = match_line(line(raw_text="ITEM 91234567 COKE", name="Coke"), [COLA_12])
    assert result.reason != "SKU 123456"


def test_a_code_shorter_than_three_characters_is_ignored():
    short = Candidate(12, "Lemons", Dimension.COUNT, "each", codes=(("SKU", "12"),))
    result = match_line(line(raw_text="Limes 12 x 1", name="Limes"), [short])
    assert result.reason != "SKU 12"
