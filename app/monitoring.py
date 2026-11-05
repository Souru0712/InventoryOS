"""Error reporting. Off unless SENTRY_DSN is set, and never sends sessions, tokens or form bodies."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import get_settings

if TYPE_CHECKING:
    from sentry_sdk.types import Event, Hint

log = logging.getLogger(__name__)

FILTERED = "[filtered]"
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "stripe-signature",
    "x-square-hmacsha256-signature",
}
_initialised = False


def scrub(event: Event, _hint: Hint | None = None) -> Event:
    """Strip anything that could carry a session, a token or a customer's data from an event."""
    request = event.get("request")
    if not isinstance(request, dict):
        return event
    request.pop("cookies", None)
    # Form posts carry passwords and invoice contents.
    request.pop("data", None)
    # Customer login links put their token in the query string.
    if request.get("query_string"):
        request["query_string"] = FILTERED
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {
            name: FILTERED if name.lower() in SENSITIVE_HEADERS else value
            for name, value in headers.items()
        }
    elif isinstance(headers, list):
        request["headers"] = [
            [name, FILTERED if str(name).lower() in SENSITIVE_HEADERS else value]
            for name, value in headers
        ]
    return event


def init_monitoring(component: str) -> bool:
    """Start reporting unhandled errors and ERROR log lines for this process, once."""
    global _initialised
    settings = get_settings()
    if _initialised or not settings.sentry_dsn:
        return False

    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.release or None,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=scrub,
    )
    sentry_sdk.set_tag("component", component)
    _initialised = True
    log.info("error reporting enabled for %s", component)
    return True
