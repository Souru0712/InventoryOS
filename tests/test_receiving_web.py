"""The receiving pages treat every upload and form field as untrusted input (S3, S12)."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Document, Location, Tenant
from app.storage import get_storage
from tests.sample_files import jpeg, pdf, png

HTML = b"<script>alert(document.cookie)</script>"


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        test_client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "o@rosewood.test",
                "password": "a-good-password",
            },
        )
        yield test_client


@pytest.fixture
def location_id(client) -> int:
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id="loc-1", name="Main"
        )
        db.add(location)
        db.commit()
        return location.id


def upload(client, location_id, filename, content, content_type):
    return client.post(
        "/app/receiving",
        data={"location_id": location_id},
        files={"photo": (filename, content, content_type)},
        follow_redirects=False,
    )


def only_document() -> Document:
    with SessionLocal() as db:
        return db.query(Document).one()


def test_html_named_jpg_and_sent_as_html_is_refused(client, location_id):
    """Replaces audit probe R7: this upload used to be served back as text/html."""
    response = upload(client, location_id, "x.jpg", HTML, "text/html")

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        assert db.query(Document).count() == 0


def test_a_real_photo_sent_as_html_is_stored_and_served_as_an_image(client, location_id):
    photo = jpeg("delivery")
    upload(client, location_id, "x.jpg", photo, "text/html")

    document = only_document()
    assert document.content_type == "image/jpeg"
    served = client.get(f"/app/receiving/{document.id}/image")
    assert served.status_code == 200
    assert served.content == photo
    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["content-disposition"] == f'inline; filename="document-{document.id}.jpg"'


def test_the_stored_type_comes_from_the_bytes_not_the_file_name(client, location_id):
    content = png("screenshot")
    upload(client, location_id, "screenshot.jpg", content, "image/jpeg")

    document = only_document()
    assert document.content_type == "image/png"
    assert document.storage_key.endswith(f"{hashlib.sha256(content).hexdigest()}.png")
    assert client.get(f"/app/receiving/{document.id}/image").headers["content-type"] == "image/png"


def test_a_pdf_is_recognised_by_its_header(client, location_id):
    upload(client, location_id, "statement.pdf", pdf("statement"), "application/octet-stream")

    assert only_document().content_type == "application/pdf"


def test_an_upload_over_15_mb_is_refused(client, location_id):
    assert get_settings().max_upload_mb == 15
    too_big = jpeg("big") + b"\0" * (15 * 1024 * 1024)

    response = upload(client, location_id, "big.jpg", too_big, "image/jpeg")

    assert "error=" in response.headers["location"]
    with SessionLocal() as db:
        assert db.query(Document).count() == 0


def test_a_record_stored_before_the_fix_is_still_served_safely(client, location_id):
    """Uploads made before T0.06 kept the uploader's Content-Type; serving no longer reads it."""
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        key = f"{tenant.id}/old-upload.jpg"
        get_storage().put(key, HTML, "text/html")
        document = Document(
            tenant_id=tenant.id,
            location_id=location_id,
            storage_key=key,
            content_type="text/html",
            sha256=hashlib.sha256(HTML).hexdigest(),
            byte_size=len(HTML),
        )
        db.add(document)
        db.commit()
        document_id = document.id

    served = client.get(f"/app/receiving/{document_id}/image")

    assert served.headers["content-type"] == "image/jpeg"
    assert served.headers["x-content-type-options"] == "nosniff"


def test_a_stored_file_of_an_unknown_type_downloads_instead_of_rendering(client, location_id):
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        key = f"{tenant.id}/odd-upload.html"
        get_storage().put(key, HTML, "text/html")
        document = Document(
            tenant_id=tenant.id,
            location_id=location_id,
            storage_key=key,
            content_type="text/html",
            sha256=hashlib.sha256(HTML + b"odd").hexdigest(),
            byte_size=len(HTML),
        )
        db.add(document)
        db.commit()
        document_id = document.id

    served = client.get(f"/app/receiving/{document_id}/image")

    assert served.headers["content-type"] == "application/octet-stream"
    assert served.headers["content-disposition"].startswith("attachment;")


def test_a_file_posted_under_a_text_field_counts_as_empty(client, location_id):
    upload(client, location_id, "note.jpg", jpeg("a delivery note"), "image/jpeg")
    document_id = only_document().id

    # A file part where the form expects text used to reach .strip() and fail with a 500.
    response = client.post(
        f"/app/receiving/{document_id}/apply",
        files={"supplier": ("supplier.txt", b"Northside", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Document, document_id).supplier is None
