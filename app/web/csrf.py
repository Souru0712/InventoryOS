"""CSRF protection (SEC-05, S4): a state-changing request must carry this session's form token.

The token lives in the signed session cookie, is put into every form as a hidden field (and
into the scanner's requests as a header), and is compared in constant time. Provider webhooks
are exempt: they carry no cookie and prove themselves with a signature.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from collections.abc import Awaitable, Callable

from markupsafe import Markup, escape
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger(__name__)

CSRF_KEY = "csrf"
FIELD = "csrf_token"
HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Signed by the provider, sent without a browser's cookies.
EXEMPT_PATHS = {"/webhooks/square", "/billing/webhook"}


def csrf_token(request: Request) -> str:
    """This session's form token, made on first use."""
    token = request.session.get(CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_KEY] = token
    return str(token)


def csrf_field(request: Request) -> Markup:
    """The hidden input every POST form carries."""
    return Markup(  # noqa: S704 (the token is escaped; the markup around it is ours)
        f'<input type="hidden" name="{FIELD}" value="{escape(csrf_token(request))}">'
    )


def _replay(body: bytes) -> Callable[[], Awaitable[Message]]:
    """A receive callable that hands the already-read body on, once."""
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive


class CSRFProtect:
    """Refuses a state-changing request whose token is missing or not this session's.

    Runs inside the session middleware, so the expected token is already decoded.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] in SAFE_METHODS
            or scope["path"] in EXEMPT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        body = await Request(scope, receive).body()
        expected = scope.get("session", {}).get(CSRF_KEY)
        sent: object = Request(scope).headers.get(HEADER)
        if not sent:
            form = await Request(scope, _replay(body)).form()
            sent = form.get(FIELD)
            await form.close()
        if (
            not expected
            or not isinstance(sent, str)
            or not hmac.compare_digest(sent.encode(), str(expected).encode())
        ):
            log.warning(
                "refused a %s to %s without a valid form token", scope["method"], scope["path"]
            )
            response = PlainTextResponse(
                "This form has expired. Go back, reload the page and try again.", status_code=403
            )
            await response(scope, _replay(b""), send)
            return
        await self.app(scope, _replay(body), send)
