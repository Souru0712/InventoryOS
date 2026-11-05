"""Security headers on every response (SEC-08, SEC-09; audit finding S10)."""

from __future__ import annotations

import secrets

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Pages may run only this origin's scripts and the inline blocks carrying this request's nonce;
# inline event handlers and injected <script> tags do not run. Inline styles stay allowed: the
# templates use style attributes throughout, and a style cannot run code. Google Fonts serves
# the stylesheet and the font files from two hosts.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'nonce-{nonce}'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
    ]
)

# One year. No includeSubDomains: other hosts under the same domain are not ours to commit.
HSTS = "max-age=31536000"


class SecurityHeaders:
    """Adds the headers to every HTTP response; a header a route set itself is left alone.

    Templates put `request.state.csp_nonce` on their inline <script> tags.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16)
        scope.setdefault("state", {})["csp_nonce"] = nonce
        # Behind the platform's TLS proxy the scheme comes from X-Forwarded-Proto
        # (uvicorn --proxy-headers).
        https = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", CSP.format(nonce=nonce))
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                if https:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
