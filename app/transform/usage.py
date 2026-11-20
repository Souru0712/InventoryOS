"""Direct usage rules (T2.14, STD-FR-04): ingredients used without a sale, with a reason."""

from __future__ import annotations

import re
from dataclasses import dataclass

SOURCE = "usage"
REASONS: dict[str, str] = {
    "staff_meal": "Staff meal",
    "sample": "Sample or tasting",
    "catering": "Catering by weight",
    "other": "Other",
}
MAX_LINES = 8
_TOKEN = re.compile(r"[0-9a-f]{32}")


class UsageRuleError(ValueError):
    """A usage record that cannot be posted, with the reason to show."""


@dataclass(frozen=True)
class Why:
    reason: str
    note: str | None

    @property
    def text(self) -> str:
        """What the ledger shows: the reason, and the note after it."""
        return REASONS[self.reason] + (f": {self.note}" if self.note else "")


def check_reason(reason: str, note: str | None) -> Why:
    """A reason is required (STD-FR-04); "Other" needs a note saying what it was."""
    if reason not in REASONS:
        raise UsageRuleError("Choose why it was used.")
    cleaned = " ".join((note or "").split())[:200] or None
    if reason == "other" and cleaned is None:
        raise UsageRuleError("Say what it was used for.")
    return Why(reason, cleaned)


def check_token(token: str) -> str:
    """The form's one-time token: the same form sent twice posts once."""
    cleaned = (token or "").strip().lower()
    if not _TOKEN.fullmatch(cleaned):
        raise UsageRuleError("The form expired: open it again.")
    return cleaned
