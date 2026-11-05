"""Read the date printed on a package from a photo of it.

The model transcribes what is printed and offers its own reading. The transcription is then
parsed by transform/dates, and the two are compared: agreement is what earns a high enough
confidence to apply without asking. Deciding the date here would put that logic somewhere
untestable.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

from app.config import get_settings
from app.extract.images import prepare_for_model

log = logging.getLogger(__name__)

SUPPORTED_MEDIA = {"image/jpeg", "image/png", "image/webp"}

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": ["string", "null"],
            "description": "every character printed in the image, exactly as printed",
        },
        "label": {
            "type": ["string", "null"],
            "description": "the words printed next to the date: EXP, BEST BEFORE, USE BY, PKD",
        },
        "date_iso": {
            "type": ["string", "null"],
            "description": "the date you read, as YYYY-MM-DD, or null if none is legible",
        },
        "legible": {"type": "boolean", "description": "false if the print is too blurred to read"},
        "confidence": {
            "type": "number",
            "description": "0 to 1: how sure you are of the characters, not of the meaning",
        },
    },
    "required": ["text", "label", "date_iso", "legible", "confidence"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You read the best-before and expiry dates printed on food and retail packaging.

Transcribe only what is printed. Never invent a date.
- text is every character you can see, including lot codes, exactly as printed.
- label is the wording next to the date, such as EXP, BB, BEST BEFORE, USE BY, SELL BY, PKD.
- date_iso is your own reading of the date as YYYY-MM-DD. A date printed as 10/11/26 is
  ambiguous: read it the way the rest of the packaging suggests, and lower your confidence.
- If the printing is smudged, partly cut off or out of focus, set legible to false and say what
  you can still see in text.
- confidence is about the characters you read, not about whether the date makes sense."""


@dataclass
class ExpiryRead:
    text: str | None = None
    label: str | None = None
    date_iso: str | None = None
    legible: bool = False
    confidence: float = 0.0
    raw: dict = field(default_factory=dict)
    reader: str = "stub"


class ExpiryReadError(RuntimeError):
    """The photo could not be read; the caller must offer manual entry."""


class ExpiryReader(Protocol):
    name: str

    def read(self, image: bytes, media_type: str) -> ExpiryRead: ...


class ClaudeExpiryReader:
    """Vision with structured output, so the JSON always parses."""

    name = "claude"

    def __init__(self, model: str | None = None):
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ExpiryReadError("ANTHROPIC_API_KEY is not set, cannot read dates from photos")
        try:
            import anthropic
        except ImportError as exc:
            raise ExpiryReadError("the anthropic package is not installed") from exc

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = model or settings.extractor_model

    def read(self, image: bytes, media_type: str) -> ExpiryRead:
        if media_type not in SUPPORTED_MEDIA:
            raise ExpiryReadError(f"unsupported image type {media_type!r}")
        if not image:
            raise ExpiryReadError("the photo was empty")

        # A date is photographed close up on a 12-megapixel phone; send what the model reads.
        image, media_type = prepare_for_model(image, media_type)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "name": "record_date",
                    "description": "Record the date printed on the packaging.",
                    "input_schema": READ_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": "record_date"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.standard_b64encode(image).decode(),
                            },
                        },
                        {"type": "text", "text": "Read the date printed here."},
                    ],
                }
            ],
        )
        for block in response.content:
            if getattr(block, "type", "") == "tool_use":
                return _to_read(block.input, self.name)
        raise ExpiryReadError("the reader returned nothing usable")


class StubExpiryReader:
    """Returns a fixed payload, so the flow can be tested without a network."""

    name = "stub"

    def __init__(self, payload: dict | None = None):
        self._payload = payload or {"legible": False, "confidence": 0.0}

    def read(self, image: bytes, media_type: str) -> ExpiryRead:
        return _to_read(self._payload, self.name)


def _to_read(payload: dict, reader: str) -> ExpiryRead:
    try:
        confidence = float(payload.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return ExpiryRead(
        text=(payload.get("text") or None),
        label=(payload.get("label") or None),
        date_iso=(payload.get("date_iso") or None),
        legible=bool(payload.get("legible")),
        confidence=min(max(confidence, 0.0), 1.0),
        raw=payload,
        reader=reader,
    )


def get_reader() -> ExpiryReader:
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeExpiryReader()
    log.warning("ANTHROPIC_API_KEY is unset; dates must be typed in by hand")
    return StubExpiryReader()


def dump(payload: dict) -> str:
    return json.dumps(payload, default=str)[:10000]
