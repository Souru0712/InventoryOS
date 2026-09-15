"""Pure reading of a date printed on packaging.

Packaging prints dates in every format there is, and the same six digits can be two different
days: 10/11/26 is October 11th in Chicago and November 10th in London. Nothing here guesses
silently - anything uncertain comes back with a low confidence so a person is asked.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

# Longest first, so "best before" wins over "bb" and "packed on" over "packed".
KEYWORDS: list[tuple[str, str]] = [
    ("best before end", "best_before"),
    ("best before", "best_before"),
    ("bestbefore", "best_before"),
    ("best by", "best_before"),
    ("display until", "sell_by"),
    ("manufactured on", "packed"),
    ("manufactured", "packed"),
    ("packed on", "packed"),
    ("produced on", "packed"),
    ("use before", "use_by"),
    ("expiration", "expiry"),
    ("expires on", "expiry"),
    ("expires", "expiry"),
    ("sell by", "sell_by"),
    ("use by", "use_by"),
    ("expiry", "expiry"),
    ("packed", "packed"),
    ("bbe", "best_before"),
    ("pkd", "packed"),
    ("mfg", "packed"),
    ("exp", "expiry"),
    ("bb", "best_before"),
    ("ub", "use_by"),
]

# A date that only tells you when something was made is not a date to sell against.
NOT_AN_EXPIRY = {"packed"}

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH_WORDS = "|".join(sorted(MONTHS, key=len, reverse=True))

ISO = re.compile(r"\b(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})\b")
# Dot-matrix printers space their separators out, so "24 . 10 . 26" is one date, not three numbers.
_SEP = r"(?:\s*[-/.]\s*|\s+)"
NUMERIC = re.compile(rf"\b(?P<a>\d{{1,2}}){_SEP}(?P<b>\d{{1,2}})(?:{_SEP}(?P<c>\d{{2,4}}))?\b")
DAY_MONTH_WORD = re.compile(
    rf"\b(?P<d>\d{{1,2}})\s*[-/. ]?\s*(?P<mon>{_MONTH_WORDS})\s*[-/. ,]?\s*(?P<y>\d{{2,4}})?\b",
    re.IGNORECASE,
)
MONTH_WORD_DAY = re.compile(
    rf"\b(?P<mon>{_MONTH_WORDS})\s*[-/. ]?\s*(?P<d>\d{{1,2}})\s*[-/. ,]+\s*(?P<y>\d{{2,4}})\b",
    re.IGNORECASE,
)
MONTH_WORD_YEAR = re.compile(rf"\b(?P<mon>{_MONTH_WORDS})\s*[-/. ,]?\s*(?P<y>\d{{2,4}})\b", re.IGNORECASE)
COMPACT8 = re.compile(r"\b(?P<digits>\d{8})\b")
COMPACT6 = re.compile(r"\b(?P<digits>\d{6})\b")

MAX_YEARS_AHEAD = 10
ONE = Decimal("1")


@dataclass(frozen=True)
class PrintedDate:
    value: date
    date_type: str  # best_before | use_by | sell_by | expiry | packed | unknown
    confidence: Decimal  # 0 to 1
    raw: str
    note: str | None = None

    @property
    def is_expiry(self) -> bool:
        """A packing or manufacture date says nothing about when stock goes off."""
        return self.date_type not in NOT_AN_EXPIRY


def find_keyword(text: str) -> str:
    """Which kind of date the packaging says this is."""
    lowered = re.sub(r"[^a-z ]+", " ", (text or "").lower())
    squeezed = re.sub(r"\s+", " ", lowered)
    for word, kind in KEYWORDS:
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", squeezed):
            return kind
    return "unknown"


def _year(raw: str | int | None, today: date) -> tuple[int, bool]:
    """Return (year, was_two_digit). Two digits are read as this century."""
    if raw is None or raw == "":
        return today.year, False
    text = str(raw)
    if len(text) >= 3:
        return int(text), False
    value = int(text)
    return 2000 + value, True


def _build(year: int, month: int, day: int | None) -> date | None:
    if not 1 <= month <= 12:
        return None
    if day is None:
        # "best before end OCT 2026" means the end of that month, not the first.
        return date(year, month, calendar.monthrange(year, month)[1])
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)


def _plausible(value: date, today: date) -> bool:
    return today - timedelta(days=400) <= value <= today.replace(year=today.year + MAX_YEARS_AHEAD)


def _candidates(text: str, today: date, prefer: str) -> list[tuple[date, Decimal, str | None]]:
    """Every reading of the text, with how much each can be trusted."""
    found: list[tuple[date, Decimal, str | None]] = []

    for match in ISO.finditer(text):
        if value := _build(int(match["y"]), int(match["m"]), int(match["d"])):
            found.append((value, ONE, None))

    for match in MONTH_WORD_DAY.finditer(text):
        year, short = _year(match["y"], today)
        if value := _build(year, MONTHS[match["mon"].lower()], int(match["d"])):
            found.append((value, Decimal("0.97") if short else ONE, None))

    for match in DAY_MONTH_WORD.finditer(text):
        year, short = _year(match["y"], today)
        if value := _build(year, MONTHS[match["mon"].lower()], int(match["d"])):
            found.append((value, Decimal("0.97") if short else ONE, None))

    for match in MONTH_WORD_YEAR.finditer(text):
        year, short = _year(match["y"], today)
        if value := _build(year, MONTHS[match["mon"].lower()], None):
            found.append((value, Decimal("0.92") if short else Decimal("0.95"), "month only"))

    for match in NUMERIC.finditer(text):
        a, b = int(match["a"]), int(match["b"])
        year, short = _year(match["c"], today)
        penalty = Decimal("0.97") if short else ONE

        if match["c"] is None:
            # Two numbers alone: a month and a year, so the end of that month.
            if value := _build(_year(b, today)[0], a, None):
                found.append((value, Decimal("0.85"), "month and year only"))
            continue
        if a > 12 and b <= 12:  # 24/10/26 can only be day first
            if value := _build(year, b, a):
                found.append((value, penalty, None))
        elif b > 12 and a <= 12:  # 10/24/26 can only be month first
            if value := _build(year, a, b):
                found.append((value, penalty, None))
        else:
            # Both fit either slot. Read it the way the merchant's country writes dates,
            # and say so, because the other reading is a different day.
            month, day = (a, b) if prefer == "mdy" else (b, a)
            if value := _build(year, month, day):
                found.append((value, penalty * Decimal("0.7"), "day and month could be swapped"))

    for match in COMPACT8.finditer(text):
        digits = match["digits"]
        for year, month, day in (
            (int(digits[:4]), int(digits[4:6]), int(digits[6:])),
            (int(digits[4:]), int(digits[2:4]), int(digits[:2])),
        ):
            if value := _build(year, month, day):
                found.append((value, Decimal("0.8"), "no separators"))

    for match in COMPACT6.finditer(text):
        digits = match["digits"]
        pairs = (
            [(digits[:2], digits[2:4], digits[4:]), (digits[2:4], digits[:2], digits[4:])]
            if prefer == "mdy"
            else [(digits[2:4], digits[:2], digits[4:]), (digits[:2], digits[2:4], digits[4:])]
        )
        for month, day, year in pairs:
            if value := _build(_year(year, today)[0], int(month), int(day)):
                found.append((value, Decimal("0.6"), "six digits, no separators"))
                break

    return found


def parse_printed_date(
    text: str, today: date | None = None, prefer: str = "mdy", ocr_confidence: Decimal | float = 1
) -> PrintedDate | None:
    """Read the date off a scrap of packaging text.

    prefer decides ambiguous numeric dates: "mdy" for the US, "dmy" elsewhere. Confidence
    folds in how sure the reader was of the characters and how sure we are of the reading.
    """
    if not text or not text.strip():
        return None
    today = today or date.today()
    cleaned = re.sub(r"\s+", " ", text.strip())

    candidates = _candidates(cleaned, today, prefer)
    if not candidates:
        return None

    # Prefer a plausible date, then the most trustworthy reading, then the earliest.
    candidates.sort(key=lambda c: (not _plausible(c[0], today), -c[1], c[0]))
    value, parse_confidence, note = candidates[0]

    kind = find_keyword(cleaned)
    confidence = Decimal(str(ocr_confidence)) * parse_confidence
    if kind == "unknown":
        # No "EXP" or "BEST BEFORE" nearby, so it may not be a date about shelf life at all.
        confidence *= Decimal("0.9")
        note = note or "no date label printed"
    if not _plausible(value, today):
        confidence *= Decimal("0.5")
        note = "date is outside the range stock normally carries"

    return PrintedDate(
        value=value,
        date_type=kind,
        confidence=min(confidence, ONE).quantize(Decimal("0.01")),
        raw=cleaned[:200],
        note=note,
    )


def prefer_for_currency(currency: str | None) -> str:
    """US packaging writes month first; almost everywhere else writes the day first."""
    return "mdy" if (currency or "USD").upper() == "USD" else "dmy"
