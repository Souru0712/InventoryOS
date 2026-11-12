"""A slow request must not stall the others on the same worker (C8, T0.07)."""

import asyncio
import inspect
import re
import threading
import time

import httpx
import pytest
from fastapi.routing import APIRoute

from app.db import Base, SessionLocal, engine
from app.extract.invoice import StubExtractor
from app.jobs import drain
from app.main import app
from app.models import Location, Tenant
from tests.accounts import confirm_email
from tests.sample_files import jpeg


class HeldExtractor:
    """Blocks like a slow model call until the test lets it go."""

    name = "held"

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def extract(self, content, filename):
        self.started.set()
        self.release.wait(timeout=5)
        self.finished.set()
        return StubExtractor({"lines": []}).extract(content, filename)


def api_routes(routes):
    """Every APIRoute, including those inside included routers (which FastAPI keeps nested)."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from api_routes(inner.routes)
        elif isinstance(route, APIRoute):
            yield route


def test_no_route_runs_on_the_event_loop():
    """Every route calls synchronous database or API code, so none may be `async def`; FastAPI
    runs plain `def` routes in its threadpool. Reading a body belongs in app/web/inputs.py."""
    routes = list(api_routes(app.routes))
    assert len(routes) > 50, "the walk must reach the routers' routes"
    assert [r.path for r in routes if inspect.iscoroutinefunction(r.endpoint)] == []


@pytest.mark.asyncio
async def test_a_slow_extraction_does_not_hold_up_other_requests(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    held = HeldExtractor()
    monkeypatch.setattr("app.orchestration.receiving.get_extractor", lambda: held)

    # One event loop for every request, as on a single uvicorn worker.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:

        async def form_token(page: str) -> str:
            """What a browser posts back: the token in the page's form (T1.07)."""
            html = (await client.get(page)).text
            return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

        await client.post(
            "/signup",
            data={
                "business_name": "Rosewood",
                "email": "o@rosewood.test",
                "password": "a-good-password",
                "csrf_token": await form_token("/signup"),
            },
        )
        confirm_email("o@rosewood.test")
        with SessionLocal() as db:
            tenant = db.query(Tenant).one()
            location = Location(
                tenant_id=tenant.id, provider="demo", provider_location_id="loc-1", name="Main"
            )
            db.add(location)
            db.commit()
            location_id = location.id

        token = await form_token("/app/receiving")
        uploaded = await client.post(
            "/app/receiving",
            data={"location_id": location_id, "csrf_token": token},
            files={"photo": ("note.jpg", jpeg("slow"), "image/jpeg")},
        )
        # The upload answers without reading the photo: a job reads it (T1.20, SCL-03).
        assert uploaded.status_code == 303
        assert not held.started.is_set(), "the request read the photo itself"

        worker = asyncio.create_task(asyncio.to_thread(drain))
        assert await asyncio.to_thread(held.started.wait, 5), "the job never read the photo"
        started = time.perf_counter()
        health = await client.get("/healthz")
        elapsed = time.perf_counter() - started
        answered_mid_extraction = not held.finished.is_set()
        held.release.set()
        stats = await worker

    assert health.status_code == 200
    # A blocked event loop would only let /healthz through after the extraction finished.
    assert answered_mid_extraction, "/healthz waited for the extraction to finish"
    assert elapsed < 0.2, f"/healthz took {elapsed:.2f}s"
    assert stats["done"] == 1
