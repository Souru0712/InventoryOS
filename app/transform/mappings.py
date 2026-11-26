"""POS product mapping rules (T3.04; POS-FR-06, PD-17). Pure: no ORM, no I/O.

A POS variation maps onto one of the business's product sizes, a POS modifier onto one of its
modifiers. Suggestions match by name; reprocessing after a mapping covers at most 30 days, and
by default starts at the last count of what the product uses (PD-17).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

ACTIVE, SUPERSEDED = "active", "superseded"
MAPPING_STATUSES = (ACTIVE, SUPERSEDED)
REPROCESS_DAYS = 30  # PD-17: the furthest back a reprocessing reaches
SUGGESTIONS = 3
REGULAR = "regular"  # the size a product without sizes is sold as


@dataclass(frozen=True)
class Candidate:
    """One of the business's product sizes, as a suggestion sees it."""

    variation_id: int
    product: str
    size: str


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _score(name: str, size: str, candidate: Candidate) -> int:
    product, wanted = _words(candidate.product), _words(name)
    if not product or not wanted:
        return 0
    # The same product outranks a right size on another product: sizes are easy to correct.
    score = 4 if product == wanted else 1 if product in wanted or wanted in product else 0
    if score == 0:
        return 0
    have, want = _words(candidate.size), _words(size)
    if have == want or (not want and have == REGULAR):
        score += 2
    return score


def suggest(name: str, size: str, candidates: Iterable[Candidate]) -> list[int]:
    """The best-matching sizes for a POS product, by name then size; at most three."""
    scored = [(_score(name, size, c), c) for c in candidates]
    ranked = sorted(
        ((score, c) for score, c in scored if score > 0),
        key=lambda pair: (-pair[0], pair[1].product.casefold(), pair[1].size.casefold()),
    )
    return [c.variation_id for _, c in ranked[:SUGGESTIONS]]


def default_start(today: date, last_count: date | None) -> date:
    """Where a reprocessing starts unless the person chooses: the last count of what the product
    uses, so a count that already absorbed the missing use is not undone, and never more than
    30 days back (PD-17)."""
    floor = earliest_start(today)
    if last_count is None:
        return floor
    return min(max(last_count, floor), today)


def earliest_start(today: date) -> date:
    return today - timedelta(days=REPROCESS_DAYS)


class MappingRuleError(ValueError):
    """A mapping change the rules refuse; nothing is written."""


def check_start(start: date, today: date) -> date:
    if start > today:
        raise MappingRuleError("Reprocessing cannot start in the future.")
    if start < earliest_start(today):
        raise MappingRuleError(f"Reprocessing reaches back {REPROCESS_DAYS} days at most.")
    return start
