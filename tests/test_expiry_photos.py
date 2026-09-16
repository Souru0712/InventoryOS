"""Scoring real photographs, and what happens to the date once a person confirms it."""
import io
import json
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image

from app.config import get_settings
from app.extract.expiry import ExpiryRead, StubExpiryReader
from app.models import StockBatch, StockItem, TrackMode
from app.orchestration.expiry_corpus import (
    add_to_corpus,
    as_regression_cases,
    load_cases,
    report,
    run_corpus,
)
from app.orchestration.scanning import CONFIDENT_ENOUGH, read_expiry_photo, receive_scanned
from app.units import Dimension, to_base

TODAY = date(2026, 9, 16)


class ScriptedReader:
    """One transcription per photo, keyed by file name: a stand-in for the vision model."""

    name = "scripted"

    def __init__(self, by_size: dict):
        self.by_size = by_size

    def read(self, image: bytes, media_type: str) -> ExpiryRead:
        payload = self.by_size[len(image)]
        return ExpiryRead(
            text=payload["text"], label=payload.get("label"), date_iso=payload.get("date_iso"),
            legible=payload.get("legible", True), confidence=payload.get("confidence", 0.95),
            raw=payload, reader=self.name,
        )


def write_photo(folder, name: str, width: int) -> int:
    """Each photo gets a different width so the scripted reader can tell them apart."""
    image = Image.new("RGB", (width, 200), (240, 240, 240))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    (folder / name).write_bytes(buffer.getvalue())
    return len(buffer.getvalue())


@pytest.fixture
def corpus_folder(tmp_path):
    """Three photographed packages: one clear, one ambiguous, one the reader gets wrong."""
    sizes = {}
    sizes["clear.jpg"] = write_photo(tmp_path, "clear.jpg", 300)
    sizes["ambiguous.jpg"] = write_photo(tmp_path, "ambiguous.jpg", 320)
    sizes["misread.jpg"] = write_photo(tmp_path, "misread.jpg", 340)

    (tmp_path / "cases.json").write_text(json.dumps([
        {"file": "clear.jpg", "expected": "2026-10-24", "note": "large, obvious"},
        {"file": "ambiguous.jpg", "expected": "2026-11-10", "prefer": "dmy", "note": "10/11/26"},
        {"file": "misread.jpg", "expected": "2026-10-24", "note": "low contrast"},
    ]), encoding="utf-8")

    reader = ScriptedReader({
        sizes["clear.jpg"]: {"label": "BEST BY", "text": "BEST BY 10/24/26", "date_iso": "2026-10-24"},
        sizes["ambiguous.jpg"]: {"label": "EXP", "text": "EXP 10/11/26", "date_iso": "2026-11-10"},
        # Reads 2028 where the packaging says 2026: legible, confident, and wrong.
        sizes["misread.jpg"]: {"label": "BEST BY", "text": "BEST BY 10/24/28", "date_iso": "2028-10-24"},
    })
    return tmp_path, reader


def test_the_corpus_scores_each_photo_as_pass_asked_or_wrong(corpus_folder):
    folder, reader = corpus_folder
    results = {result.file: result for result in run_corpus(folder, today=TODAY, reader=reader)}

    assert results["clear.jpg"].verdict == "pass"
    # Right answer, but the app asks because the print could be read either way round.
    assert results["ambiguous.jpg"].verdict == "asked"
    # Confidently wrong is the only failure that actually costs the owner stock.
    assert results["misread.jpg"].verdict == "WRONG"


def test_the_report_leads_with_whether_this_beats_typing(corpus_folder):
    folder, reader = corpus_folder
    text = report(run_corpus(folder, today=TODAY, reader=reader))

    assert "1/3 read correctly with no question asked" in text
    assert "wrong and applied unasked" in text
    assert "A wrong date applied unasked is a bug" in text


def test_what_went_wrong_becomes_a_regression_case(corpus_folder, tmp_path):
    folder, reader = corpus_folder
    results = run_corpus(folder, today=TODAY, reader=reader)

    cases = as_regression_cases(results)
    assert {case["id"] for case in cases} == {"ambiguous", "misread"}, "passes are not worth keeping"
    assert cases[0]["text"], "the transcription is what the parser needs to reproduce it"

    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({"today": "2026-09-16", "cases": []}), encoding="utf-8")
    assert add_to_corpus(cases, corpus_path) == 2
    assert add_to_corpus(cases, corpus_path) == 0, "a rerun must not duplicate cases"


def test_a_corpus_without_its_answers_says_so(tmp_path):
    with pytest.raises(FileNotFoundError, match="cases.json"):
        load_cases(tmp_path)


def test_a_photo_listed_but_missing_is_named(tmp_path):
    (tmp_path / "cases.json").write_text(json.dumps([{"file": "gone.jpg", "expected": None}]), "utf-8")
    with pytest.raises(FileNotFoundError, match="gone.jpg"):
        load_cases(tmp_path)


# --- the date that ends up on the shelf ---------------------------------------------------------

def test_the_confidence_is_kept_with_the_batch_not_just_the_date(db, tenant, location):
    """Months later, "why is this lot dated 24 Oct" is answerable only if the read is on record."""
    item = StockItem(tenant_id=tenant.id, location_id=location.id, slug="yoghurt",
                     name="Natural yoghurt", dimension=Dimension.COUNT, stock_unit="each",
                     track_mode=TrackMode.MANUAL, qty_base=Decimal("10"))
    db.add(item)
    db.commit()

    receive_scanned(db, item, to_base(6, "each"), expires_on=date(2026, 10, 24),
                    date_type="best_before", date_confidence=Decimal("0.97"))
    db.commit()

    batch = db.query(StockBatch).one()
    assert batch.expires_on == date(2026, 10, 24)
    assert batch.date_type == "best_before"
    assert batch.date_confidence == Decimal("0.97")


def test_a_reading_the_app_is_unsure_of_is_handed_to_a_person(db):
    """An ambiguous print must arrive at the screen marked for confirmation, not applied."""
    reader = StubExpiryReader({"text": "EXP 10/11/26", "label": "EXP", "date_iso": "2026-10-11",
                               "legible": True, "confidence": 0.98})
    reading = read_expiry_photo(b"photo", "image/jpeg", prefer="mdy", today=TODAY, reader=reader)

    assert reading.value == date(2026, 10, 11)
    assert reading.needs_confirmation is True
    assert reading.confidence < CONFIDENT_ENOUGH
    assert "swapped" in (reading.note or "")


def test_a_phone_sized_photo_of_a_date_is_accepted_now_that_it_is_resized():
    """A 12-megapixel close-up used to be refused outright as too large."""
    from app.web.scanning import _photo_limit

    assert _photo_limit() == get_settings().max_upload_mb * 1024 * 1024
    assert _photo_limit() >= 8 * 1024 * 1024, "a phone photo must fit"
