"""The receiving review form treats whatever is posted as untrusted input."""

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Document, Location, Tenant


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def _uploaded_document(client) -> int:
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
    client.post(
        "/app/receiving",
        data={"location_id": location_id},
        files={"photo": ("note.jpg", b"\xff\xd8\xff a delivery note", "image/jpeg")},
        follow_redirects=False,
    )
    with SessionLocal() as db:
        return db.query(Document).one().id


def test_a_file_posted_under_a_text_field_counts_as_empty(client):
    document_id = _uploaded_document(client)

    # A file part where the form expects text used to reach .strip() and fail with a 500.
    response = client.post(
        f"/app/receiving/{document_id}/apply",
        files={"supplier": ("supplier.txt", b"Northside", "text/plain")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.get(Document, document_id).supplier is None
