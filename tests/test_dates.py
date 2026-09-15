"""Reading the date printed on a package, including the ones that read two ways."""
from datetime import date
from decimal import Decimal

import pytest

from app.transform.dates import (
    find_keyword,
    parse_printed_date,
    prefer_for_currency,
)

TODAY = date(2026, 9, 13)


def parse(text, **kwargs):
    kwargs.setdefault("today", TODAY)
    return parse_printed_date(text, **kwargs)


# --- formats --------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "printed",
    [
        "EXP 10/24/26",
        "EXP 10-24-2026",
        "EXP 2026-10-24",
        "BB 24 OCT 2026",
        "BEST BEFORE OCT 24 2026",
        "USE BY 24OCT26",
        "EXP 20261024",
    ],
)
def test_the_same_day_in_every_spelling(printed):
    assert parse(printed).value == date(2026, 10, 24)


def test_a_bare_month_and_year_means_the_end_of_that_month():
    # "Best before end October" is sold right through the 31st.
    assert parse("BEST BEFORE END OCT 2026").value == date(2026, 10, 31)
    assert parse("BB 10/26").value == date(2026, 10, 31)


def test_a_two_digit_year_is_this_century():
    assert parse("EXP 24 OCT 26").value.year == 2026


def test_text_with_no_date_in_it_reads_as_nothing():
    assert parse("Coca-Cola 12oz can") is None
    assert parse("") is None
    assert parse(None) is None


# --- which kind of date ---------------------------------------------------------------------

@pytest.mark.parametrize(
    "printed, kind",
    [
        ("USE BY 24 OCT 2026", "use_by"),
        ("BEST BEFORE 24 OCT 2026", "best_before"),
        ("BBE 24 OCT 2026", "best_before"),
        ("SELL BY 24 OCT 2026", "sell_by"),
        ("DISPLAY UNTIL 24 OCT 2026", "sell_by"),
        ("EXP 24 OCT 2026", "expiry"),
        ("24 OCT 2026", "unknown"),
    ],
)
def test_the_label_says_what_kind_of_date_it_is(printed, kind):
    assert parse(printed).date_type == kind


def test_a_packing_date_is_not_an_expiry():
    # The trap: PKD is when it was made, and selling against it would be wrong.
    for printed in ("PKD 24 OCT 2026", "PACKED ON 24 OCT 2026", "MFG 24 OCT 2026"):
        result = parse(printed)
        assert result.date_type == "packed"
        assert result.is_expiry is False


def test_bb_inside_a_word_is_not_a_label():
    assert find_keyword("RIBBON 24 OCT 2026") == "unknown"


# --- the ambiguous ones ---------------------------------------------------------------------

def test_a_number_above_twelve_settles_the_order_on_its_own():
    assert parse("EXP 24/10/26", prefer="mdy").value == date(2026, 10, 24)
    assert parse("EXP 10/24/26", prefer="dmy").value == date(2026, 10, 24)


def test_a_date_that_reads_two_ways_follows_the_merchants_country():
    assert parse("EXP 10/11/26", prefer="mdy").value == date(2026, 10, 11)
    assert parse("EXP 10/11/26", prefer="dmy").value == date(2026, 11, 10)


def test_a_date_that_reads_two_ways_is_not_trusted_enough_to_apply_silently():
    result = parse("EXP 10/11/26", prefer="mdy")
    assert result.confidence < Decimal("0.95")
    assert "swapped" in result.note


def test_an_unambiguous_date_is_trusted():
    assert parse("EXP 24 OCT 2026").confidence >= Decimal("0.95")


def test_six_digits_with_no_separators_are_read_but_barely_trusted():
    result = parse("EXP 102426")
    assert result.value == date(2026, 10, 24)
    assert result.confidence < Decimal("0.8")


def test_currency_decides_how_a_country_writes_dates():
    assert prefer_for_currency("USD") == "mdy"
    assert prefer_for_currency("GBP") == "dmy"
    assert prefer_for_currency(None) == "mdy"


# --- confidence -----------------------------------------------------------------------------

def test_a_blurry_photo_drags_the_confidence_down_with_it():
    sharp = parse("EXP 24 OCT 2026", ocr_confidence=1)
    blurry = parse("EXP 24 OCT 2026", ocr_confidence=0.6)
    assert blurry.confidence < sharp.confidence
    assert blurry.confidence == pytest.approx(Decimal("0.6"), abs=Decimal("0.02"))


def test_a_date_with_no_label_is_trusted_a_little_less():
    labelled = parse("EXP 24 OCT 2026")
    bare = parse("24 OCT 2026")
    assert bare.confidence < labelled.confidence


def test_a_date_years_in_the_past_is_flagged_rather_than_accepted():
    result = parse("EXP 24 OCT 2019")
    assert result.confidence < Decimal("0.6")
    assert "outside the range" in result.note


def test_a_date_far_beyond_any_shelf_life_is_flagged():
    result = parse("EXP 24 OCT 2099")
    assert result.confidence < Decimal("0.6")


def test_a_date_just_past_is_still_read_normally():
    # Stock that has already gone off is exactly what we want to catch, not reject.
    result = parse("EXP 01 SEP 2026")
    assert result.value == date(2026, 9, 1)
    assert result.confidence >= Decimal("0.9")


def test_confidence_never_exceeds_certainty():
    assert parse("EXP 2026-10-24", ocr_confidence=1).confidence <= Decimal("1")


# --- real packaging strings -----------------------------------------------------------------

@pytest.mark.parametrize(
    "printed, expected",
    [
        ("USE BY\n24 OCT 2026", date(2026, 10, 24)),
        ("BEST BEFORE: 24.10.2026", date(2026, 10, 24)),
        ("EXP  24 . 10 . 26", date(2026, 10, 24)),
        ("L2847  BB 24OCT2026", date(2026, 10, 24)),
        ("BEST BEFORE END: OCTOBER 2026", date(2026, 10, 31)),
    ],
)
def test_dates_as_they_actually_appear_on_packaging(printed, expected):
    assert parse(printed, prefer="dmy").value == expected


# --- from a photo: the reader and the parser checking each other ------------------------------

from decimal import Decimal as D  # noqa: E402

from app.extract.expiry import StubExpiryReader  # noqa: E402
from app.orchestration.scanning import read_expiry_photo  # noqa: E402

IMAGE = b"pretend-this-is-a-photo"


def read(payload, **kwargs):
    kwargs.setdefault("today", TODAY)
    kwargs.setdefault("prefer", "dmy")
    return read_expiry_photo(IMAGE, "image/jpeg", reader=StubExpiryReader(payload), **kwargs)


def test_a_clear_photo_is_applied_without_asking():
    result = read({"text": "24 OCT 2026", "label": "BEST BEFORE", "date_iso": "2026-10-24",
                   "legible": True, "confidence": 1.0})
    assert result.value == date(2026, 10, 24)
    assert result.date_type == "best_before"
    assert result.needs_confirmation is False


def test_the_two_readings_disagreeing_forces_a_question():
    # The parser reads the characters; the model read something else from the same photo.
    result = read({"text": "24 OCT 2026", "label": "BB", "date_iso": "2026-11-24",
                   "legible": True, "confidence": 1.0})
    assert result.value == date(2026, 10, 24)  # the deterministic reading wins
    assert result.needs_confirmation is True
    assert result.note == "the photo could be read two ways"


def test_an_ambiguous_date_is_never_applied_silently():
    result = read({"text": "10/11/26", "label": "EXP", "date_iso": "2026-11-10",
                   "legible": True, "confidence": 1.0})
    assert result.value == date(2026, 11, 10)  # dmy, as a GBP merchant would print it
    assert result.needs_confirmation is True


def test_a_packing_date_is_always_questioned():
    result = read({"text": "24 OCT 2026", "label": "PKD", "date_iso": "2026-10-24",
                   "legible": True, "confidence": 1.0})
    assert result.date_type == "packed"
    assert result.is_expiry is False
    # High confidence in the characters, but it is still the wrong kind of date to apply.
    assert result.confidence >= D("0.9")
    assert result.needs_confirmation is True


def test_a_blurred_photo_asks_rather_than_guesses():
    result = read({"text": "24 0CT 2026", "label": "BB", "date_iso": "2026-10-24",
                   "legible": False, "confidence": 0.55})
    assert result.needs_confirmation is True


def test_an_unreadable_photo_comes_back_empty_so_the_user_can_type_it():
    result = read({"text": None, "label": None, "date_iso": None,
                   "legible": False, "confidence": 0.0})
    assert result.value is None
    assert result.needs_confirmation is True
    assert result.note


def test_a_format_the_parser_does_not_know_still_uses_the_readers_answer():
    # Nothing here parses as a date, but the reader saw one.
    result = read({"text": "L2847 BATCH", "label": None, "date_iso": "2026-10-24",
                   "legible": True, "confidence": 0.9})
    assert result.value == date(2026, 10, 24)
    assert result.needs_confirmation is True  # trusted less, so confirmed
    assert "familiar format" in result.note


def test_a_vaguer_reading_than_the_readers_one_is_questioned():
    # "October 2026" is the whole month; the reader claims a specific day. Ask.
    result = read({"text": "expires October, 2026", "label": None, "date_iso": "2026-10-24",
                   "legible": True, "confidence": 0.9})
    assert result.value == date(2026, 10, 31)
    assert result.needs_confirmation is True
