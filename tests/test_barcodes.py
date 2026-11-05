"""Barcodes are strings with leading zeros, several spellings, and a check digit."""

import pytest

from app.transform.barcodes import (
    canonical,
    check_digit,
    clean,
    expand_upc_e,
    has_valid_check_digit,
    lookup_keys,
)


@pytest.mark.parametrize(
    "raw",
    ["041000123456", " 041000123456 ", "041000123456\n", "0410 0012 3456", "041-000-123456"],
)
def test_a_scanner_can_add_whitespace_and_dashes(raw):
    assert clean(raw) == "041000123456"


def test_a_leading_zero_survives():
    # The difference between the right product and the wrong one.
    value, _ = canonical("041000123456")
    assert value == "041000123456"
    assert value != "41000123456"


def test_check_digits_match_the_published_examples():
    assert check_digit("03600029145") == "2"  # UPC-A
    assert check_digit("400638133393") == "1"  # EAN-13
    assert check_digit("9638507") == "4"  # EAN-8


def test_a_misread_is_caught():
    assert has_valid_check_digit("036000291452")
    assert not has_valid_check_digit("036000291453")


def test_check_digits_are_only_claimed_for_lengths_that_have_one():
    assert not has_valid_check_digit("12345")
    assert not has_valid_check_digit("ABC123")


@pytest.mark.parametrize(
    "short, long",
    [
        ("04252614", "042100005264"),  # last data digit 0-2
        ("04252635", "042500000265"),  # 3
        ("04252641", "042520000061"),  # 4
        ("04252672", "042526000072"),  # 5-9
    ],
)
def test_upc_e_expands_to_the_form_a_catalogue_stores(short, long):
    assert expand_upc_e(short) == long
    # The expansion is only right if the check digit still agrees with the rest.
    assert has_valid_check_digit(long)


def test_only_a_real_upc_e_expands():
    assert expand_upc_e("4252614") is None  # too short
    assert expand_upc_e("94252614") is None  # number system 9 is not UPC-E
    assert expand_upc_e("ABCDEFGH") is None


def test_upc_e_is_expanded_when_the_scanner_says_so():
    assert canonical("04252614", "upc_e") == ("042100005264", "upc")


def test_an_eight_digit_code_is_ean_8_unless_the_scanner_says_upc_e():
    assert canonical("96385074") == ("96385074", "ean")
    assert canonical("96385074", "ean_8") == ("96385074", "ean")


def test_lengths_decide_the_type_when_the_scanner_is_silent():
    assert canonical("036000291452")[1] == "upc"
    assert canonical("4006381333931")[1] == "ean"


def test_a_non_numeric_code_is_kept_as_printed():
    assert canonical("ABC-123", "code_128") == ("ABC123", "other")


def test_the_same_product_is_found_under_either_spelling():
    # Square may hold the 12-digit UPC while the phone reports the 13-digit EAN.
    assert lookup_keys("036000291452") == ["036000291452", "0036000291452"]
    assert lookup_keys("0036000291452") == ["0036000291452", "036000291452"]


def test_a_thirteen_digit_code_that_is_not_a_upc_has_one_spelling():
    assert lookup_keys("4006381333931") == ["4006381333931"]


def test_nothing_scanned_is_nothing_looked_up():
    assert lookup_keys("") == []
    assert lookup_keys(None) == []
    assert canonical(None) == ("", "other")
