"""Pure shopper model: what a customer told us, took home and rated, as the box builder may use it."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

RATINGS = ("loved", "liked", "neutral", "disliked", "never_again")
RATING_POINTS = {"loved": 40, "liked": 15, "neutral": 0, "disliked": -40}
TOP_POINTS = 40
# Ratings before an inferred category lean counts at half strength: one bad chip is a hint, not a verdict.
CATEGORY_PRIOR = 3
# Reviews before a product's rating across all customers counts at half strength.
QUALITY_PRIOR = 3
SPICE_LEVELS = ("none", "mild", "medium", "hot")
NOVELTY_LEVELS = ("familiar", "balanced", "surprise")


@dataclass(frozen=True)
class Review:
    stock_item_id: int
    rating: str
    send_again: bool
    submitted_at: datetime


@dataclass(frozen=True)
class Received:
    """One item in one box the customer has been packed."""

    order_id: int
    stock_item_id: int
    packed_at: datetime


@dataclass(frozen=True)
class Stated:
    """What the customer set on their preferences page. Explicit answers outrank anything inferred."""

    allergens: frozenset[str] = frozenset()
    spice: str = "medium"
    novelty: str = "balanced"
    liked_categories: frozenset[str] = frozenset()
    avoided_categories: frozenset[str] = frozenset()
    favorites: frozenset[int] = frozenset()
    never_again: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Taste:
    allergens: frozenset[str] = frozenset()
    never_again: frozenset[int] = frozenset()
    repeat: frozenset[int] = frozenset()
    loved: frozenset[int] = frozenset()
    affinity: dict[int, int] = field(default_factory=dict)
    category: dict[str, float] = field(default_factory=dict)  # -1..1, lower-case names
    category_source: dict[str, str] = field(default_factory=dict)
    boxes_since: dict[int, int] = field(default_factory=dict)
    boxes_received: int = 0
    spice: str = "medium"
    novelty: str = "balanced"
    last_categories: frozenset[str] = frozenset()
    last_origins: frozenset[str] = frozenset()

    def received(self, stock_item_id: int) -> bool:
        return stock_item_id in self.boxes_since

    @property
    def known(self) -> bool:
        """Anything to personalize on yet; a first box gets the house ranking."""
        return bool(self.boxes_received or self.affinity or self.never_again)


def build_taste(
    stated: Stated,
    reviews: Iterable[Review],
    received: Iterable[Received],
    categories: dict[int, str],
    origins: dict[int, str],
) -> Taste:
    latest: dict[int, Review] = {}
    for review in sorted(reviews, key=lambda r: r.submitted_at):
        latest[review.stock_item_id] = review

    # The preferences page owns never-again, so undoing it there sticks even though the survey said it.
    never = frozenset(stated.never_again)
    affinity = {i: RATING_POINTS[r.rating] for i, r in latest.items() if r.rating in RATING_POINTS}
    for item_id in stated.favorites:
        affinity[item_id] = RATING_POINTS["loved"]
    loved = frozenset(i for i, r in latest.items() if r.rating == "loved") | stated.favorites
    repeat = frozenset(i for i, r in latest.items() if r.send_again) - never

    by_category: dict[str, list[int]] = {}
    for item_id, points in affinity.items():
        name = (categories.get(item_id) or "").lower()
        if name:
            by_category.setdefault(name, []).append(points)
    category = {
        name: sum(v) / len(v) / TOP_POINTS * len(v) / (len(v) + CATEGORY_PRIOR)
        for name, v in by_category.items()
    }
    source = {name: f"{len(v)} rating{'s' if len(v) != 1 else ''}" for name, v in by_category.items()}
    for name in stated.liked_categories:
        category[name.lower()], source[name.lower()] = 1.0, "they said so"
    for name in stated.avoided_categories:
        category[name.lower()], source[name.lower()] = -1.0, "they said so"

    rows = sorted(received, key=lambda r: (r.packed_at, r.order_id))
    boxes = list(dict.fromkeys(r.order_id for r in rows))
    seat = {order_id: i for i, order_id in enumerate(boxes)}
    last_box: dict[int, int] = {}
    for row in rows:
        last_box[row.stock_item_id] = row.order_id
    latest_items = [r.stock_item_id for r in rows if boxes and r.order_id == boxes[-1]]

    return Taste(
        allergens=frozenset(stated.allergens),
        never_again=never,
        repeat=repeat,
        loved=loved,
        affinity=affinity,
        category=category,
        category_source=source,
        boxes_since={i: len(boxes) - 1 - seat[o] for i, o in last_box.items()},
        boxes_received=len(boxes),
        spice=stated.spice,
        novelty=stated.novelty,
        last_categories=frozenset((categories.get(i) or "").lower() for i in latest_items) - {""},
        last_origins=frozenset(origins.get(i) or "" for i in latest_items) - {""},
    )


def crowd_quality(ratings: Iterable[tuple[int, str]], prior: int = QUALITY_PRIOR) -> dict[int, float]:
    """Stock item -> how every customer rated it, -1..1, shrunk toward 0 until there are enough reviews."""
    points: dict[int, list[int]] = {}
    for item_id, rating in ratings:
        points.setdefault(item_id, []).append(RATING_POINTS.get(rating, -TOP_POINTS))
    return {
        item_id: round(sum(v) / len(v) / TOP_POINTS * len(v) / (len(v) + prior), 3)
        for item_id, v in points.items()
    }
