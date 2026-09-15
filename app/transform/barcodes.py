"""Pure barcode handling: turn whatever a scanner reports into one canonical code.

Barcodes are strings, never numbers. 041000123456 and 41000123456 are different products,
so a leading zero that an integer would drop is the difference between the right item and
the wrong one.
"""
from __future__ import annotations

import re

# What BarcodeDetector and ZXing call a symbology, mapped to what we store it as.
SYMBOLOGIES = {
    "upc_a": "upc",
    "upc_e": "upc",
    "ean_13": "ean",
    "ean_8": "ean",
    "code_128": "other",
    "code_39": "other",
    "code_93": "other",
    "codabar": "other",
    "itf": "other",
}

_STRIP = re.compile(r"[\s\-‐-―]+")
_DIGITS = re.compile(r"^\d+$")


def clean(raw: str | None) -> str:
    """Remove what a scanner, a barcode font or a paste adds around the code itself."""
    if not raw:
        return ""
    return _STRIP.sub("", str(raw)).upper()


def is_numeric(code: str) -> bool:
    return bool(_DIGITS.match(code))


def check_digit(body: str) -> str:
    """The GS1 mod-10 check digit for everything but the check digit itself.

    Weights run 3,1,3,1 from the right, which is correct for UPC-A, EAN-8 and EAN-13 alike.
    """
    if not is_numeric(body):
        raise ValueError(f"{body!r} is not numeric")
    total = sum(int(digit) * (3 if index % 2 == 0 else 1) for index, digit in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def has_valid_check_digit(code: str) -> bool:
    """True when the last digit agrees with the rest. Catches a misread before it hits the data."""
    if not is_numeric(code) or len(code) not in (8, 12, 13, 14):
        return False
    return check_digit(code[:-1]) == code[-1]


def expand_upc_e(code: str) -> str | None:
    """UPC-E is a squeezed UPC-A. Small packages carry it; catalogues store the long form."""
    if not is_numeric(code) or len(code) != 8:
        return None
    system, body, check = code[0], code[1:7], code[7]
    if system not in ("0", "1"):
        return None
    x1, x2, x3, x4, x5, x6 = body
    if x6 in ("0", "1", "2"):
        middle = f"{x1}{x2}{x6}0000{x3}{x4}{x5}"
    elif x6 == "3":
        middle = f"{x1}{x2}{x3}00000{x4}{x5}"
    elif x6 == "4":
        middle = f"{x1}{x2}{x3}{x4}00000{x5}"
    else:
        middle = f"{x1}{x2}{x3}{x4}{x5}0000{x6}"
    return f"{system}{middle}{check}"


def canonical(raw: str | None, symbology: str | None = None) -> tuple[str, str]:
    """(value, identifier_type) for storage and lookup.

    UPC-E is expanded, because the same product scans short on the shelf and long in Square.
    """
    code = clean(raw)
    if not code:
        return "", "other"

    kind = SYMBOLOGIES.get((symbology or "").lower().replace("-", "_"), "")
    if not is_numeric(code):
        return code, kind or "other"

    if len(code) == 8 and (symbology or "").lower().replace("-", "_") == "upc_e":
        if expanded := expand_upc_e(code):
            return expanded, "upc"
    if len(code) == 12:
        return code, "upc"
    if len(code) in (8, 13):
        return code, "ean"
    return code, kind or "other"


def lookup_keys(raw: str | None, symbology: str | None = None) -> list[str]:
    """Every form the same product might already be stored under, most specific first.

    A 12-digit UPC-A and the 13-digit EAN with a leading zero are the same product, and
    scanners, suppliers and Square do not agree on which one to report.
    """
    value, _ = canonical(raw, symbology)
    if not value:
        return []

    keys = [value]
    if is_numeric(value):
        if len(value) == 12:
            keys.append(f"0{value}")
        elif len(value) == 13 and value.startswith("0"):
            keys.append(value[1:])
        if len(value) == 8 and (expanded := expand_upc_e(value)):
            keys.append(expanded)
    return list(dict.fromkeys(keys))
