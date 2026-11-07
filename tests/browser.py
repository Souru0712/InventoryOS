"""Tests post the way a browser does after loading a page: with the session's form token.

Every state-changing request needs it (T1.07). A test that sends its own X-CSRF-Token header or
csrf_token field, even an empty one, is left exactly as it wrote it, which is how the CSRF tests
send none. Imported once by conftest; the guard keeps a second import from wrapping twice.
"""

import base64
import json

from itsdangerous import TimestampSigner
from starlette.testclient import TestClient

SAFE = {"GET", "HEAD", "OPTIONS"}


def session_of(client: TestClient) -> dict:
    """The signed session cookie's contents, as the app sees them."""
    cookie = client.cookies.get("session")
    if not cookie:
        return {}
    return json.loads(base64.b64decode(TimestampSigner("test-secret-key").unsign(cookie)))


def _carries_token(kwargs: dict) -> bool:
    headers = {key.lower() for key in (kwargs.get("headers") or {})}
    data = kwargs.get("data")
    return "x-csrf-token" in headers or (isinstance(data, dict) and "csrf_token" in data)


if not getattr(TestClient.request, "as_a_browser", False):
    send = TestClient.request

    def request_as_a_browser(self, method, url, *args, **kwargs):
        if method.upper() not in SAFE and not _carries_token(kwargs):
            token = session_of(self).get("csrf")
            if token is None:
                send(self, "GET", "/login")  # any page with a form issues one
                token = session_of(self).get("csrf")
            kwargs["headers"] = {**(kwargs.get("headers") or {}), "X-CSRF-Token": token}
        return send(self, method, url, *args, **kwargs)

    request_as_a_browser.as_a_browser = True  # type: ignore[attr-defined]
    TestClient.request = request_as_a_browser
