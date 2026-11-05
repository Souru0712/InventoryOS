"""The corpus: every date shape the app has met, and every one it has got wrong.

Each case is text as transcribed from real packaging. A photo proves the reader; this proves what
the app does with what the reader saw, which is where a wrong date actually comes from - and it
runs with no API key, no network and no photograph.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.orchestration.scanning import CONFIDENT_ENOUGH
from app.transform.dates import parse_printed_date

CORPUS = json.loads((Path(__file__).parent / "corpus" / "printed_dates.json").read_text("utf-8"))
TODAY = date.fromisoformat(CORPUS["today"])
CASES = CORPUS["cases"]


def ids(cases) -> list[str]:
    return [case["id"] for case in cases]


@pytest.mark.parametrize("case", CASES, ids=ids(CASES))
def test_the_corpus_reads_the_way_it_is_supposed_to(case):
    expected = case["expect"]
    parsed = parse_printed_date(case["text"], today=TODAY, prefer=case["prefer"])

    if expected.get("date") is None:
        assert parsed is None, f"{case['id']}: found a date where there is none"
        return

    assert parsed is not None, f"{case['id']}: no date found in {case['text']!r}"
    assert parsed.value.isoformat() == expected["date"], case["text"]
    if "type" in expected:
        assert parsed.date_type == expected["type"], case["text"]
    if "is_expiry" in expected:
        assert parsed.is_expiry is expected["is_expiry"]

    # The question is what the app does, which is DateReading.needs_confirmation: a clear
    # reading of a packing date still gets asked about, because it is not a date to sell against.
    applies_unasked = parsed.confidence >= CONFIDENT_ENOUGH and parsed.is_expiry
    assert applies_unasked is expected["confident"], (
        f"{case['id']}: confidence {parsed.confidence}, type {parsed.date_type}; "
        f"{'applied without asking' if applies_unasked else 'asked about'} instead"
    )
    if expected.get("note"):
        assert expected["note"] in (parsed.note or ""), f"{case['id']}: note was {parsed.note!r}"


def test_the_same_print_read_in_two_countries_gives_two_different_days():
    """The heart of the ambiguity rule: neither reading may be applied without asking."""
    us = parse_printed_date("EXP 10/11/26", today=TODAY, prefer="mdy")
    uk = parse_printed_date("EXP 10/11/26", today=TODAY, prefer="dmy")

    assert us.value != uk.value
    assert us.confidence < CONFIDENT_ENOUGH and uk.confidence < CONFIDENT_ENOUGH


def test_every_corpus_case_has_an_id_and_an_expectation():
    """A corpus entry with nothing asserted is a case that silently passes forever."""
    assert len(ids(CASES)) == len(set(ids(CASES))), "duplicate case ids"
    for case in CASES:
        assert case.get("text") and case.get("prefer") in ("mdy", "dmy")
        assert "date" in case["expect"]
