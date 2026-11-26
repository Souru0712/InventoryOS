"""Onboarding rules (T2.20-b; SAAS §5 step 0, WF-01). Pure.

Step 0 asks how the business takes payments today and routes it: a supported POS connects
(Complete); everything else starts Standalone, recording the POS a business would want supported
(adapter demand) or its interest in a Vontry POS (Scenario C). Plans are not changed here.
"""

from __future__ import annotations

from collections.abc import Iterable

ANSWERS: dict[str, str] = {
    "pos_supported": "I use Square",
    "none": "No POS, and I don't want one",
    "none_wants_pos": "No POS, but I want one",
    "pos_unsupported": "I use a POS you don't support yet",
}
STEPS = (
    "location",
    "items",
    "suppliers",
    "recipes",
    "sales",
    "mapping",  # Connected only (SAAS §5 step 8, T3.10)
    "alerts",
    "count",
    "receiving",
    "team",
)


class OnboardingRuleError(ValueError):
    """An answer the rules refuse, with the reason to show."""


def check_answer(answer: str, pos_name: str) -> tuple[str, str | None]:
    """The answer and the POS it names: Square for a supported one, the name given for one not
    supported yet (it is the demand an adapter answers to), none otherwise."""
    if answer not in ANSWERS:
        raise OnboardingRuleError("Choose how you take payments today.")
    name = " ".join(pos_name.split())[:80] or None
    if answer == "pos_unsupported" and name is None:
        raise OnboardingRuleError("Tell us which POS you use, so we know to support it.")
    if answer == "pos_supported":
        return answer, "Square"
    return answer, name if answer == "pos_unsupported" else None


def path(answer: str | None) -> str:
    """Connected when a supported POS is in use, Standalone otherwise (and before step 0)."""
    return "connected" if answer == "pos_supported" else "standalone"


def skipped(raw: str | None) -> set[str]:
    return {key for key in (raw or "").split(",") if key in STEPS}


def with_skip(raw: str | None, step: str, skip: bool) -> str:
    """The stored list of skipped steps with one step skipped or taken back up."""
    if step not in STEPS:
        raise OnboardingRuleError("There is no such step.")
    keys = skipped(raw)
    keys = keys | {step} if skip else keys - {step}
    return ",".join(key for key in STEPS if key in keys)


def remaining(done: Iterable[tuple[str, bool]], raw_skipped: str | None) -> list[str]:
    """Steps neither done nor skipped, in order."""
    away = skipped(raw_skipped)
    return [key for key, finished in done if not finished and key not in away]
