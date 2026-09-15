"""Pure receiving logic: match invoice lines to stock, resolve packs into base units.

Receiving is the job that hurts: a delivery arrives and someone re-types every
count. Getting it wrong is what makes an online store sell what is not there.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher

from app.units import Dimension, UnitError, dimension_of, to_base

ZERO = Decimal("0")
AUTO_MATCH_THRESHOLD = Decimal("0.82")
SUGGEST_THRESHOLD = Decimal("0.55")

# Words that carry no identity, only packaging or marketing.
NOISE_WORDS = {
    "organic",
    "fresh",
    "premium",
    "brand",
    "pack",
    "packs",
    "case",
    "cases",
    "box",
    "boxes",
    "carton",
    "cartons",
    "tray",
    "trays",
    "bag",
    "bags",
    "sack",
    "sacks",
    "bundle",
    "bundles",
    "crate",
    "crates",
    "bottle",
    "bottles",
    "tub",
    "tubs",
    "tin",
    "tins",
    "jar",
    "jars",
    "jug",
    "jugs",
    "pk",
    "ct",
    "ea",
    "each",
    "x",
    "of",
    "the",
    "and",
}

PACK_WORDS = {"case", "box", "carton", "pack", "tray", "crate", "bag", "sack", "bundle", "dozen"}

# Supplier unit spellings -> our unit codes.
UNIT_SYNONYMS = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gr": "g", "gm": "g", "gram": "g", "grams": "g",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb", "#": "lb",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "l": "l", "lt": "l", "ltr": "l", "litre": "l", "litres": "l", "liter": "l", "liters": "l",
    "ml": "ml", "mls": "ml", "millilitre": "ml", "milliliter": "ml",
    "floz": "floz", "fl": "floz", "cup": "cup", "cups": "cup",
    "pt": "pt", "pint": "pt", "pints": "pt",
    "qt": "qt", "quart": "qt", "quarts": "qt",
    "gal": "gal", "gallon": "gal", "gallons": "gal",
    "each": "each", "ea": "each", "unit": "each", "units": "each", "pc": "each", "pcs": "each",
    "dozen": "dozen", "dz": "dozen",
}

_PACK_PATTERN = re.compile(
    r"(?P<count>\d+(?:\.\d+)?)\s*(?:x|\*|/)\s*(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>[a-z#]+)",
    re.IGNORECASE,
)
_SIZE_PATTERN = re.compile(r"(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>[a-z#]+)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ParsedLine:
    """One line as read off the invoice, before any matching."""

    line_no: int
    raw_text: str
    name: str
    qty: Decimal
    unit: str | None = None
    pack_size: Decimal | None = None
    unit_price_cents: int | None = None
    line_total_cents: int | None = None
    expires_on: date | None = None


@dataclass(frozen=True)
class Candidate:
    """A stock item an invoice line could refer to."""

    stock_item_id: int
    name: str
    dimension: Dimension
    stock_unit: str
    receive_unit: str = "each"
    # (label, code) pairs from product_identifiers, most specific first: a UPC before a SKU.
    codes: tuple[tuple[str, str], ...] = ()
    category: str | None = None
    pack_size_base: Decimal | None = None
    aliases: tuple[str, ...] = ()


@dataclass
class MatchResult:
    line: ParsedLine
    stock_item_id: int | None
    confidence: Decimal
    reason: str
    received_qty_base: Decimal | None = None
    problem: str | None = None
    runners_up: list[tuple[int, Decimal]] = field(default_factory=list)

    @property
    def is_auto(self) -> bool:
        return (
            self.stock_item_id is not None
            and self.confidence >= AUTO_MATCH_THRESHOLD
            and self.received_qty_base is not None
            and self.problem is None
        )


def normalize_name(text: str) -> str:
    """Lowercase, strip accents, drop packaging noise and sizes."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    folded = folded.lower()
    folded = _PACK_PATTERN.sub(" ", folded)
    folded = _SIZE_PATTERN.sub(" ", folded)
    folded = re.sub(r"[^a-z0-9\s]", " ", folded)
    tokens = [t for t in folded.split() if t and t not in NOISE_WORDS and not t.isdigit()]
    return " ".join(tokens)


def similarity(left: str, right: str) -> Decimal:
    """Blend token overlap with sequence similarity; both are cheap and complementary."""
    a, b = normalize_name(left), normalize_name(right)
    if not a or not b:
        return ZERO
    if a == b:
        return Decimal("1")

    tokens_a, tokens_b = set(a.split()), set(b.split())
    overlap = len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
    sequence = SequenceMatcher(None, a, b).ratio()
    blended = Decimal(str(0.6 * overlap + 0.4 * sequence))
    return blended.quantize(Decimal("0.001"))


def parse_pack(text: str) -> tuple[Decimal, Decimal, str] | None:
    """Read '6 x 1L' or '12x500g' as (count, size, unit_code)."""
    match = _PACK_PATTERN.search(text or "")
    if not match:
        return None
    unit = UNIT_SYNONYMS.get(match.group("unit").lower())
    if not unit:
        return None
    return Decimal(match.group("count")), Decimal(match.group("size")), unit


def parse_size(text: str) -> tuple[Decimal, str] | None:
    """Read a bare '500g' or '1.5 kg' as (size, unit_code)."""
    for match in _SIZE_PATTERN.finditer(text or ""):
        unit = UNIT_SYNONYMS.get(match.group("unit").lower())
        if unit:
            return Decimal(match.group("size")), unit
    return None


def canonical_unit(raw_unit: str | None) -> str | None:
    if not raw_unit:
        return None
    return UNIT_SYNONYMS.get(raw_unit.strip().lower().rstrip("."))


def resolve_received_base(line: ParsedLine, item: Candidate) -> tuple[Decimal | None, str | None]:
    """Convert an invoice quantity into base units of the stock item.

    Returns (quantity, problem). A problem means a human has to look at it.
    """
    if line.qty <= ZERO:
        return None, "quantity on the invoice is zero or missing"

    unit = canonical_unit(line.unit)
    is_pack_word = bool(line.unit) and line.unit.strip().lower().rstrip("s.") in PACK_WORDS

    # 1. Explicit pack notation anywhere on the line: "2 cases of 6 x 1L".
    pack = parse_pack(line.raw_text) or parse_pack(line.name)
    if pack and (is_pack_word or unit is None or unit == "each"):
        count, size, pack_unit = pack
        try:
            per_pack = to_base(count * size, pack_unit)
        except UnitError as exc:
            return None, str(exc)
        if dimension_of(pack_unit) is not item.dimension:
            return None, (
                f"invoice is in {dimension_of(pack_unit).value} but {item.name} is tracked by "
                f"{item.dimension.value}"
            )
        return (line.qty * per_pack).quantize(Decimal("0.0001")), None

    # 2. A real measurement unit on the line.
    if unit and unit not in PACK_WORDS:
        if dimension_of(unit) is not item.dimension:
            return None, (
                f"invoice is in {dimension_of(unit).value} ({line.unit}) but {item.name} is "
                f"tracked by {item.dimension.value}"
            )
        quantity = line.qty * (line.pack_size or Decimal("1"))
        return to_base(quantity, unit), None

    # 3. Pack word with a known pack size on the stock item: "3 cases".
    if is_pack_word or unit is None:
        if item.pack_size_base and item.pack_size_base > ZERO:
            return (line.qty * item.pack_size_base).quantize(Decimal("0.0001")), None
        size = parse_size(line.raw_text) or parse_size(line.name)
        if size and dimension_of(size[1]) is item.dimension:
            return to_base(line.qty * size[0], size[1]), None
        if item.dimension is Dimension.COUNT:
            return to_base(line.qty, item.receive_unit or "each"), None
        return None, (
            f"{item.name} is tracked by {item.dimension.value}, but the invoice gives a plain "
            "count with no pack size; set a pack size on the item"
        )

    return None, f"could not interpret unit {line.unit!r}"


def line_cost_per_base(
    line_total_cents: int | None,
    unit_price_cents: int | None,
    parsed_qty: Decimal | None,
    received_qty_base: Decimal | None,
) -> Decimal | None:
    """What one base unit cost on this invoice; None when no price was printed."""
    if received_qty_base is None or received_qty_base <= ZERO:
        return None
    if line_total_cents is not None:
        total = Decimal(line_total_cents)
    elif unit_price_cents is not None and parsed_qty:
        total = Decimal(unit_price_cents) * Decimal(parsed_qty)
    else:
        return None
    return (total / Decimal(received_qty_base)).quantize(Decimal("0.0001"))


def prints_code(text: str, code: str | None) -> bool:
    """True when a product code appears on the line as a whole token.

    Plain substring matching would let a short code hit inside a quantity or a longer code.
    """
    code = (code or "").strip().lower()
    if len(code) < 3:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(code)}(?![a-z0-9])", (text or "").lower()) is not None


def match_line(
    line: ParsedLine,
    candidates: list[Candidate],
    alias_index: dict[str, int] | None = None,
) -> MatchResult:
    """Pick the best stock item for one invoice line."""
    alias_index = alias_index or {}
    by_id = {c.stock_item_id: c for c in candidates}

    # A printed code beats learned wording: names are normalized with sizes stripped, so
    # "Cola 12oz" and "Cola 20oz" share an alias, and only the code tells them apart.
    for candidate in candidates:
        for label, code in candidate.codes:
            if prints_code(line.raw_text, code):
                return _resolved(line, candidate, Decimal("0.99"), f"{label} {code}")

    normalized = normalize_name(line.name)
    if (item_id := alias_index.get(normalized)) and item_id in by_id:
        return _resolved(line, by_id[item_id], Decimal("1"), "learned from a previous invoice")

    for candidate in candidates:
        if normalized and normalized in {normalize_name(a) for a in candidate.aliases}:
            return _resolved(line, candidate, Decimal("0.97"), "known supplier wording")

    scored = sorted(
        ((similarity(line.name, c.name), c) for c in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not scored or scored[0][0] < SUGGEST_THRESHOLD:
        return MatchResult(line, None, ZERO, "no match", problem="no stock item looks like this line")

    score, best = scored[0]
    runners = [(c.stock_item_id, s) for s, c in scored[1:4] if s >= SUGGEST_THRESHOLD]
    result = _resolved(line, best, score, f"name match ({score:.0%})")
    result.runners_up = runners
    return result


def _resolved(line: ParsedLine, item: Candidate, score: Decimal, reason: str) -> MatchResult:
    quantity, problem = resolve_received_base(line, item)
    return MatchResult(line, item.stock_item_id, score, reason, quantity, problem)


def match_document(
    lines: list[ParsedLine],
    candidates: list[Candidate],
    alias_index: dict[str, int] | None = None,
) -> list[MatchResult]:
    return [match_line(line, candidates, alias_index) for line in lines]


def build_alias_index(aliases: list[tuple[str, int]]) -> dict[str, int]:
    """Normalized supplier wording -> stock item id."""
    return {normalize_name(text): item_id for text, item_id in aliases if normalize_name(text)}
