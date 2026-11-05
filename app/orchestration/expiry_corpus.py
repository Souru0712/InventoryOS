"""Run a folder of photographed packages through the date reader and score it.

This is what turns "the API returned a date from my screenshot" into "a box of real products off
the shelf reads correctly". Point it at a folder of photos with a manifest of the right answers;
it reports what was read, whether the app would have applied it unasked, and what it got wrong.

A wrong date applied without asking is the only true failure: a wrong date the app asks about
costs the owner a correction, which is the manual work they were doing anyway.
"""

from __future__ import annotations

import json
import logging
import mimetypes
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.orchestration.scanning import DateReading, read_expiry_photo

log = logging.getLogger(__name__)

MANIFEST = "cases.json"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class CaseResult:
    file: str
    expected: date | None
    read: DateReading
    note: str

    @property
    def correct(self) -> bool:
        return self.read.value == self.expected

    @property
    def verdict(self) -> str:
        """Right unasked is a pass; wrong unasked is the failure that costs the owner stock."""
        if self.correct and not self.read.needs_confirmation:
            return "pass"
        if self.correct:
            return "asked"
        if not self.read.needs_confirmation:
            return "WRONG"
        return "asked-wrong"


def load_cases(folder: Path) -> list[dict]:
    """The manifest says what each photo really says. Without it there is nothing to score."""
    manifest = folder / MANIFEST
    if not manifest.exists():
        raise FileNotFoundError(
            f"{manifest} is missing. It holds the right answers, as "
            '[{"file": "milk.jpg", "expected": "2026-10-24", "note": "tiny print, curved"}]'
        )
    cases = json.loads(manifest.read_text("utf-8"))
    if isinstance(cases, dict):
        cases = cases.get("cases", [])
    missing = [case["file"] for case in cases if not (folder / case["file"]).exists()]
    if missing:
        raise FileNotFoundError(f"listed in {MANIFEST} but not in the folder: {', '.join(missing)}")
    return cases


def run_corpus(
    folder: Path,
    prefer: str = "mdy",
    today: date | None = None,
    reader=None,
) -> list[CaseResult]:
    """Read every photo in the manifest and compare it with the answer written down for it."""
    results: list[CaseResult] = []
    for case in load_cases(folder):
        path = folder / case["file"]
        media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        reading = read_expiry_photo(
            path.read_bytes(),
            media_type,
            prefer=case.get("prefer", prefer),
            today=today,
            reader=reader,
        )
        expected = date.fromisoformat(case["expected"]) if case.get("expected") else None
        results.append(CaseResult(case["file"], expected, reading, case.get("note", "")))
    return results


def report(results: list[CaseResult]) -> str:
    """A table an owner can read, with the number that matters on the last line."""
    if not results:
        return "no photos in the corpus"
    width = max(len(result.file) for result in results)
    lines = [
        f"{'photo'.ljust(width)}  {'read':<12} {'expected':<12} {'conf':>5}  verdict",
        f"{'-' * width}  {'-' * 12} {'-' * 12} {'-' * 5}  -------",
    ]
    for result in sorted(results, key=lambda r: r.verdict != "WRONG"):
        read = result.read.value.isoformat() if result.read.value else "-"
        expected = result.expected.isoformat() if result.expected else "-"
        lines.append(
            f"{result.file.ljust(width)}  {read:<12} {expected:<12} "
            f"{float(result.read.confidence):>5.2f}  {result.verdict}"
            + (f"  ({result.note})" if result.note else "")
        )

    counts = {verdict: 0 for verdict in ("pass", "asked", "asked-wrong", "WRONG")}
    for result in results:
        counts[result.verdict] += 1
    total = len(results)
    lines.append("")
    lines.append(
        f"{counts['pass']}/{total} read correctly with no question asked; "
        f"{counts['asked']} correct but asked about; "
        f"{counts['asked-wrong']} wrong and asked about; "
        f"{counts['WRONG']} wrong and applied unasked"
    )
    if counts["WRONG"]:
        lines.append(
            "A wrong date applied unasked is a bug. Add each one to the corpus and fix it."
        )
    elif counts["pass"] / total >= 0.7:
        lines.append("This is faster than typing them in.")
    else:
        lines.append("Correct, but asking too often to save much typing.")
    return "\n".join(lines)


def as_regression_cases(results: list[CaseResult], today: date | None = None) -> list[dict]:
    """Turn what went wrong into offline corpus cases, so it can never come back unnoticed.

    The photo itself is not needed: the transcription is what the parser sees, and the parser is
    where a wrong reading of a legible print comes from.
    """
    cases = []
    for result in results:
        if result.verdict == "pass" or not result.read.raw_text:
            continue
        cases.append(
            {
                "id": Path(result.file).stem.lower().replace(" ", "-"),
                "text": result.read.raw_text,
                "prefer": "mdy",
                "expect": {
                    "date": result.expected.isoformat() if result.expected else None,
                    "confident": result.expected is not None,
                },
                "from_photo": result.file,
                "note": result.note or f"read {result.read.value} at {result.read.confidence}",
            }
        )
    return cases


def add_to_corpus(cases: list[dict], corpus_path: Path) -> int:
    """Append new cases, keeping the ids unique so a rerun does not duplicate them."""
    corpus = json.loads(corpus_path.read_text("utf-8"))
    known = {case["id"] for case in corpus["cases"]}
    added = [case for case in cases if case["id"] not in known]
    corpus["cases"].extend(added)
    corpus_path.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    return len(added)
