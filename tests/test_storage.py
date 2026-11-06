"""Invoice photos live behind a storage key, so a redeploy or a bucket never loses the link."""

import hashlib
import io

import pytest
from fastapi.testclient import TestClient

from app.access import OrgContext
from app.db import Base, SessionLocal, engine
from app.extract.invoice import ExtractionError, StubExtractor
from app.main import app
from app.models import Document, DocumentStatus, Location, Tenant
from app.orchestration.receiving import parse_document, save_upload
from app.storage import LocalStorage, S3Storage, StorageError, get_storage
from tests.sample_files import jpeg


def test_local_storage_round_trips_and_passes_its_check(tmp_path):
    storage = LocalStorage(tmp_path / "uploads")
    storage.put("1/a.jpg", b"photo", "image/jpeg")
    assert storage.get("1/a.jpg") == b"photo"
    storage.check()


def test_a_key_cannot_escape_the_upload_directory(tmp_path):
    with pytest.raises(StorageError):
        LocalStorage(tmp_path / "uploads").put("../outside.jpg", b"x", "image/jpeg")


def test_a_missing_local_file_is_a_storage_error(tmp_path):
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).get("1/never-written.jpg")


class FakeS3:
    """Just enough of a boto3 S3 client to see what StockPulse asks for."""

    def __init__(self):
        self.objects = {}
        self.reachable = True

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = (Body, ContentType)

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise KeyError("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def head_bucket(self, Bucket):
        if not self.reachable:
            raise ConnectionError("unreachable")


def test_s3_storage_writes_under_its_prefix_with_the_content_type():
    client = FakeS3()
    storage = S3Storage("stockpulse-uploads", prefix="/uploads/", client=client)
    storage.put("1/a.jpg", b"photo", "image/jpeg")
    assert client.objects[("stockpulse-uploads", "uploads/1/a.jpg")] == (b"photo", "image/jpeg")
    assert storage.get("1/a.jpg") == b"photo"


def test_s3_failures_surface_as_storage_errors():
    client = FakeS3()
    storage = S3Storage("stockpulse-uploads", client=client)
    with pytest.raises(StorageError):
        storage.get("1/never-uploaded.jpg")
    client.reachable = False
    with pytest.raises(StorageError):
        storage.check()


def test_an_upload_is_saved_under_its_key_not_a_filesystem_path(db, tenant, location):
    content = jpeg("a-delivery-note")
    document = save_upload(
        db, OrgContext.system(tenant.id), location.id, "Note.JPG", content, "image/jpeg"
    )
    db.commit()
    assert document.storage_key == f"{tenant.id}/{hashlib.sha256(content).hexdigest()}.jpg"
    assert get_storage().get(document.storage_key) == content


def test_a_photo_missing_from_storage_fails_the_document_with_a_reason(db, tenant, location):
    document = save_upload(
        db,
        OrgContext.system(tenant.id),
        location.id,
        "note.jpg",
        jpeg("gone-missing"),
        "image/jpeg",
    )
    db.commit()
    (get_storage().root / document.storage_key).unlink()

    with pytest.raises(ExtractionError):
        parse_document(
            db, OrgContext.system(document.tenant_id), document.id, StubExtractor({"lines": []})
        )
    assert document.status is DocumentStatus.FAILED
    assert "could not be read" in document.error


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def test_an_invoice_photo_uploaded_through_the_app_is_served_back_from_storage(client):
    client.post(
        "/signup",
        data={
            "business_name": "Rosewood",
            "email": "o@rosewood.test",
            "password": "a-good-password",
        },
    )
    with SessionLocal() as db:
        tenant = db.query(Tenant).one()
        location = Location(
            tenant_id=tenant.id, provider="demo", provider_location_id="loc-1", name="Main"
        )
        db.add(location)
        db.commit()
        location_id = location.id

    photo = jpeg("a delivery note")
    response = client.post(
        "/app/receiving",
        data={"location_id": location_id},
        files={"photo": ("note.jpg", photo, "image/jpeg")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        document = db.query(Document).one()
    image = client.get(f"/app/receiving/{document.id}/image")
    assert image.status_code == 200
    assert image.content == photo
