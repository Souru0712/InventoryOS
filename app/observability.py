"""JSON logs that say which request, tenant and user a line belongs to, and nothing personal.

OBS-01, T0.10. Every line is one JSON object with `request_id` (a web request, one run of a
scheduled job, or the process itself for start-up lines), plus `tenant_id` and `user_id` once they
are known. Bodies, cookies, query strings
and tokens are never logged; anything shaped like an email address is masked as a last resort.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# One mutable dict per request or job. FastAPI copies the context into its threadpool for
# dependencies and routes; the copy shares this dict, so ids attached there reach every later line.
_context: ContextVar[dict[str, object] | None] = ContextVar("log_context", default=None)

# Fields copied from the context onto each line, in this order.
CONTEXT_FIELDS = ("request_id", "job", "tenant_id", "user_id")
# Fields a log call may pass with `extra=`.
EXTRA_FIELDS = ("method", "path", "status", "duration_ms")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# An incoming X-Request-ID is reused only if it is short and plain, so it cannot inject anything.
_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

access_log = logging.getLogger("app.access")

# Lines outside any request or job (start-up, shutdown) still carry an id: this process's.
PROCESS_ID = f"process-{uuid.uuid4().hex[:12]}"


def new_request_id() -> str:
    return uuid.uuid4().hex


@contextmanager
def log_context(**fields: object) -> Iterator[dict[str, object]]:
    """Ids for every line logged inside; nested contexts inherit and extend their parent's."""
    parent = _context.get() or {}
    context = {**parent, **fields}
    if not context.get("request_id"):
        context["request_id"] = new_request_id()
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def bind(**fields: object) -> None:
    """Attach ids learned part-way through (the signed-in user) to the current request."""
    context = _context.get()
    if context is not None:
        context.update(fields)


def current_request_id() -> str | None:
    context = _context.get()
    value = context.get("request_id") if context else None
    return str(value) if value else None


def redact(text: str) -> str:
    return _EMAIL.sub("[email]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        context = _context.get() or {"request_id": PROCESS_ID}
        for key in CONTEXT_FIELDS:
            if context.get(key) is not None:
                entry[key] = context[key]
        for key in EXTRA_FIELDS:
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """One JSON handler on the root logger, for the web process, the scheduler and the CLI."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn installs plain-text handlers before it imports the app; send its lines through
    # ours. Its access log is off: RequestContext writes one line per request instead, without
    # the query string, which can carry a customer's sign-in token.
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
    access.disabled = True
    # HTTP clients log every outgoing call's full URL at INFO (Square, Stripe, Anthropic);
    # query strings stay out of the log, failures still show at WARNING.
    for name in ("httpx", "httpx2", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


class RequestContext:
    """Gives each HTTP request an id, returns it as X-Request-ID, and logs one access line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else new_request_id()
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            finally:
                duration_ms = round((time.perf_counter() - started) * 1000, 1)
                access_log.info(
                    "%s %s %s",
                    scope["method"],
                    scope["path"],
                    status,
                    extra={
                        "method": scope["method"],
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": duration_ms,
                    },
                )
