"""Small real files for upload tests: since T0.06 an upload is accepted by what its bytes are."""

import io

from PIL import Image


def _image(fmt: str, label: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format=fmt)
    # Bytes after the image's end marker are ignored by readers but change the hash, so each
    # label is a distinct upload.
    return buffer.getvalue() + label.encode()


def jpeg(label: str = "") -> bytes:
    return _image("JPEG", label)


def png(label: str = "") -> bytes:
    return _image("PNG", label)


def pdf(label: str = "") -> bytes:
    return b"%PDF-1.4\n1 0 obj <<>> endobj\ntrailer <<>>\n%%EOF\n" + label.encode()
