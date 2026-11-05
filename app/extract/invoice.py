"""Read a photographed supplier invoice into structured lines."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from app.config import get_settings
from app.extract.images import prepare_for_model
from app.transform.receiving import ParsedLine

if TYPE_CHECKING:
    from anthropic.types import DocumentBlockParam, ImageBlockParam

log = logging.getLogger(__name__)

SUPPORTED_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "supplier": {"type": ["string", "null"]},
        "invoice_number": {"type": ["string", "null"]},
        "invoice_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "currency": {"type": ["string", "null"], "description": "ISO code, e.g. USD"},
        "total_cents": {"type": ["integer", "null"]},
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string", "description": "the line exactly as printed"},
                    "product_name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {
                        "type": ["string", "null"],
                        "description": "unit as written: kg, L, case, each, ...",
                    },
                    "pack_size": {
                        "type": ["number", "null"],
                        "description": "size of one pack when the line reads like 6 x 1L",
                    },
                    "unit_price_cents": {"type": ["integer", "null"]},
                    "line_total_cents": {"type": ["integer", "null"]},
                    "expiry_date": {
                        "type": ["string", "null"],
                        "description": "YYYY-MM-DD best-before, use-by or expiry date printed for this line",
                    },
                },
                "required": [
                    "raw_text",
                    "product_name",
                    "quantity",
                    "unit",
                    "pack_size",
                    "unit_price_cents",
                    "line_total_cents",
                    "expiry_date",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["supplier", "invoice_number", "invoice_date", "currency", "total_cents", "lines"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You read supplier invoices and delivery notes for small food and retail businesses.

Transcribe only what is printed. Never invent a product, quantity or price.
- One entry per delivered line item. Skip subtotals, tax lines, delivery charges and notes.
- quantity is the number of units delivered, as written on the line.
- unit is the unit as written (kg, g, lb, L, ml, case, box, each). Use null if absent.
- pack_size is the size of a single pack when the line reads like "6 x 1L" (here: 1).
- Money is in minor units: $12.50 becomes 1250. Use null when a price is not printed.
- expiry_date is a best-before, use-by or expiry date printed against that line, as YYYY-MM-DD.
  Use null unless one is printed for the line. Never infer it from the invoice or delivery date.
- If the image is unreadable or is not an invoice, return an empty lines array."""


def validate_payload(
    payload: object, schema: dict = EXTRACT_SCHEMA, path: str = "response"
) -> None:
    """Check the model's JSON against EXTRACT_SCHEMA before anything downstream trusts it.

    Structured output makes this rare, not impossible: a refusal, a truncation, or a future model
    can all produce something else, and a wrong quantity here becomes wrong stock.
    """
    types = schema.get("type")
    allowed = [types] if isinstance(types, str) else list(types or [])
    if not _is_type(payload, allowed):
        raise ExtractionError(
            f"{path}: expected {' or '.join(allowed)}, got {type(payload).__name__}"
        )
    if payload is None:
        return

    if "object" in allowed and isinstance(payload, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in payload:
                raise ExtractionError(f"{path}: missing {name!r}")
        if schema.get("additionalProperties") is False:
            for name in payload:
                if name not in properties:
                    raise ExtractionError(f"{path}: unexpected field {name!r}")
        for name, sub_schema in properties.items():
            if name in payload:
                validate_payload(payload[name], sub_schema, f"{path}.{name}")

    if "array" in allowed and isinstance(payload, list):
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(payload):
                validate_payload(item, item_schema, f"{path}[{index}]")


def _is_type(value: object, allowed: list[str]) -> bool:
    if not allowed:
        return True
    checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        # An invoice line may read 2.5 kg, so a number is either.
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    return any(checks[name](value) for name in allowed if name in checks)


@dataclass
class InvoiceExtract:
    supplier: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = None
    total_cents: int | None = None
    lines: list[ParsedLine] = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    extractor: str = "stub"


class ExtractionError(RuntimeError):
    """Extraction failed; the document is kept so a human can still act on it."""


class Extractor(Protocol):
    name: str

    def extract(self, content: bytes, filename: str) -> InvoiceExtract: ...


def _media_type(filename: str | Path) -> str:
    suffix = Path(filename).suffix.lower()
    try:
        return SUPPORTED_MEDIA[suffix]
    except KeyError:
        raise ExtractionError(
            f"unsupported file type {suffix!r}; accepted: {', '.join(sorted(SUPPORTED_MEDIA))}"
        ) from None


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _to_lines(payload: dict) -> list[ParsedLine]:
    lines: list[ParsedLine] = []
    for index, row in enumerate(payload.get("lines") or [], start=1):
        name = (row.get("product_name") or row.get("raw_text") or "").strip()
        if not name:
            continue
        pack = row.get("pack_size")
        lines.append(
            ParsedLine(
                line_no=index,
                raw_text=(row.get("raw_text") or name).strip(),
                name=name,
                qty=_decimal(row.get("quantity")),
                unit=(row.get("unit") or None),
                pack_size=_decimal(pack) if pack is not None else None,
                unit_price_cents=row.get("unit_price_cents"),
                line_total_cents=row.get("line_total_cents"),
                expires_on=_iso_date(row.get("expiry_date"), "expiry date"),
            )
        )
    return lines


def _iso_date(raw: object, label: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        log.warning("%s %r is not ISO format, ignoring", label, raw)
        return None


def _to_extract(payload: dict, extractor: str) -> InvoiceExtract:
    return InvoiceExtract(
        supplier=payload.get("supplier"),
        invoice_number=payload.get("invoice_number"),
        invoice_date=_iso_date(payload.get("invoice_date"), "invoice date"),
        currency=(payload.get("currency") or None),
        total_cents=payload.get("total_cents"),
        lines=_to_lines(payload),
        raw=payload,
        extractor=extractor,
    )


class ClaudeInvoiceExtractor:
    """Vision extraction with structured output, so the JSON always parses."""

    name = "claude"

    def __init__(self, model: str | None = None):
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise ExtractionError("ANTHROPIC_API_KEY is not set, cannot read invoice photos")
        try:
            import anthropic
        except ImportError as exc:
            raise ExtractionError("the anthropic package is not installed") from exc

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = model or settings.extractor_model

    def extract(self, content: bytes, filename: str) -> InvoiceExtract:
        media_type = _media_type(filename)
        # The stored original is never touched; this is only what goes over the wire.
        payload_bytes, media_type = prepare_for_model(content, media_type)
        data = base64.standard_b64encode(payload_bytes).decode()

        block_type = "document" if media_type == "application/pdf" else "image"
        # Type and media type come from SUPPORTED_MEDIA; the type checker only sees str.
        original = cast(
            "DocumentBlockParam | ImageBlockParam",
            {
                "type": block_type,
                "source": {"type": "base64", "media_type": media_type, "data": data},
            },
        )
        response = self._client.messages.create(
            model=self._model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        original,
                        {
                            "type": "text",
                            "text": "Extract every delivered line item from this document.",
                        },
                    ],
                }
            ],
        )

        if response.stop_reason == "refusal":
            raise ExtractionError("the model declined to read this document")

        text = next((block.text for block in response.content if block.type == "text"), "")
        if not text:
            raise ExtractionError("the model returned no content for this document")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"could not parse extraction output: {exc}") from exc

        validate_payload(payload)
        return _to_extract(payload, self.name)


class StubExtractor:
    """Deterministic extractor for tests, demos and offline onboarding."""

    name = "stub"

    def __init__(self, payload: dict | None = None):
        self._payload = payload or {"lines": []}

    def extract(self, content: bytes, filename: str) -> InvoiceExtract:
        return _to_extract(self._payload, self.name)


def get_extractor() -> Extractor:
    settings = get_settings()
    if settings.anthropic_api_key:
        return ClaudeInvoiceExtractor()
    log.warning("ANTHROPIC_API_KEY is unset; invoice lines must be entered by hand")
    return StubExtractor()
