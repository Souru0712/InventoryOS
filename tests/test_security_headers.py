"""Every response carries the security headers; pages run only their own nonce'd scripts (S10, S11)."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.web.templating import PublicSettings, templates

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "web" / "templates"


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def signed_in(client):
    client.post(
        "/signup",
        data={
            "business_name": "Rosewood",
            "email": "o@rosewood.test",
            "password": "a-good-password",
        },
    )
    return client


def assert_hardened(response):
    csp = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert re.search(r"script-src 'self' 'nonce-[\w-]+'", csp)
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.parametrize(
    "path", ["/", "/login", "/pricing", "/static/landing.css", "/no-such-page"]
)
def test_public_responses_carry_the_headers(client, path):
    assert_hardened(client.get(path))


@pytest.mark.parametrize("path", ["/app", "/app/items/new", "/app/expiring", "/app/receiving"])
def test_signed_in_pages_carry_the_headers(client, path):
    response = signed_in(client).get(path)
    assert response.status_code == 200
    assert_hardened(response)


def test_hsts_only_over_https(client):
    assert "strict-transport-security" not in client.get("/login").headers
    with TestClient(app, base_url="https://testserver") as secure:
        assert secure.get("/login").headers["strict-transport-security"] == "max-age=31536000"


def test_every_inline_script_carries_this_responses_nonce(client):
    page = signed_in(client).get("/app/items/new")
    nonce = re.search(r"'nonce-([\w-]+)'", page.headers["content-security-policy"]).group(1)

    inline = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", page.text)
    assert inline, "the item form has inline scripts"
    assert all(f'nonce="{nonce}"' in attributes for attributes in inline)


def test_each_response_gets_a_fresh_nonce(client):
    first = client.get("/login").headers["content-security-policy"]
    second = client.get("/login").headers["content-security-policy"]
    assert first != second


def test_no_template_uses_inline_event_handlers_or_unnonced_scripts():
    """CSP blocks both; a new one would silently stop working in the browser."""
    for template in TEMPLATES.rglob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+\s*=", text), f"inline handler in {template.name}"
        assert "javascript:" not in text, f"javascript: URL in {template.name}"
        for attributes in re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", text):
            assert "request.state.csp_nonce" in attributes, (
                f"script without nonce in {template.name}"
            )


def test_templates_see_only_public_settings(client):
    exposed = templates.env.globals["settings"]
    assert isinstance(exposed, PublicSettings)
    for secret in ("secret_key", "encryption_key", "stripe_secret_key", "database_url"):
        assert not hasattr(exposed, secret)
    # Still enough for the pages that use it.
    assert exposed.support_email in client.get("/login").text
