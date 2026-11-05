"""Shrink a phone photo to what the model actually reads, without touching the stored original.

A modern phone takes a 12 MB, 4000-pixel-wide photo. The model downsamples anything larger than
1568 pixels on its long edge before it looks at it, so sending the full file costs upload time and
sometimes fails outright, and buys nothing. The original stays in storage exactly as uploaded;
this is only what goes over the wire.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

# What Pillow calls each image format we accept. Phones save multi-picture JPEGs, which Pillow
# reports as MPO; to everything else they are ordinary JPEGs.
_IMAGE_FORMATS = {
    "JPEG": "image/jpeg",
    "MPO": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}

# The long edge the model resizes to anyway.
MAX_EDGE = 1568
# Comfortably under the API's 5 MB per-image limit, after base64 inflates it by a third.
MAX_BYTES = 3_500_000
JPEG_QUALITY = 85


def detect_media_type(content: bytes) -> str | None:
    """What the bytes are, read from the bytes; None when they are not a type we accept.

    The file name and the browser's Content-Type are the uploader's claims; this is the only
    thing an upload's stored and served type is decided by (S3, S12).
    """
    # PDF readers accept the header anywhere in the first kilobyte.
    if b"%PDF-" in content[:1024]:
        return "application/pdf"
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a dependency
        log.error("Pillow is not installed; no image upload can be accepted")
        return None
    try:
        # Reads the header only; decoding the pixels waits for prepare_for_model.
        with Image.open(io.BytesIO(content)) as image:
            return _IMAGE_FORMATS.get(image.format or "")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def prepare_for_model(content: bytes, media_type: str) -> tuple[bytes, str]:
    """Return the bytes to send and their media type. PDFs and anything unreadable pass through."""
    if media_type == "application/pdf":
        return content, media_type

    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow is a dependency
        log.warning("Pillow is not installed; sending the original image")
        return content, media_type

    try:
        with Image.open(io.BytesIO(content)) as opened:
            # Phones record orientation in EXIF rather than in the pixels; a sideways invoice
            # reads far worse than an upright one.
            image = ImageOps.exif_transpose(opened)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            image.thumbnail((MAX_EDGE, MAX_EDGE))
            shrunk = _encode(image, JPEG_QUALITY)
            for quality in (70, 55):
                if len(shrunk) <= MAX_BYTES:
                    break
                shrunk = _encode(image, quality)
    except Exception:
        log.exception("could not resize an upload; sending it as it came")
        return content, media_type

    if len(shrunk) >= len(content) and media_type != "image/gif":
        # Already small: re-encoding a tidy scan can make it bigger.
        return content, media_type
    log.info("resized upload for extraction: %s -> %s bytes", len(content), len(shrunk))
    return shrunk, "image/jpeg"


def _encode(image, quality: int) -> bytes:
    buffer = io.BytesIO()
    # No EXIF, no colour profile: none of it survives the model, and it is the customer's metadata.
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
