"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings, require_deployable
from app.db import init_db
from app.entitlements import NotEntitled
from app.monitoring import init_monitoring
from app.observability import RequestContext, configure_logging
from app.orchestration import scheduler
from app.web import (
    auth,
    billing,
    dashboard,
    health,
    marketing,
    ops,
    promotions,
    receiving,
    scanning,
    webhooks,
)
from app.web import (
    settings as settings_page,
)
from app.web.headers import SecurityHeaders
from app.web.templating import templates

configure_logging()
log = logging.getLogger(__name__)
settings = get_settings()
# Deployed on SQLite or with a placeholder secret, stop now rather than lose data later.
require_deployable(settings)
init_monitoring("web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Deployed, jobs run in the dedicated `python -m app.cli scheduler` process instead.
    if settings.embedded_scheduler:
        scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="StockPulse", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    # Secure cookies whenever the app is actually served over TLS.
    https_only=settings.app_base_url.startswith("https"),
    same_site="lax",
)
app.add_middleware(SecurityHeaders)
# Added last, so it wraps everything else: every line of a request, including the security
# headers' and the access line, carries its request id.
app.add_middleware(RequestContext)

static_dir = Path(__file__).resolve().parent / "web" / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(ops.router)
app.include_router(marketing.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(receiving.router)
app.include_router(health.router)
app.include_router(promotions.router)
app.include_router(scanning.router)
app.include_router(settings_page.router)
app.include_router(webhooks.router)
app.include_router(billing.router)


@app.exception_handler(NotEntitled)
async def show_what_this_needs(request: Request, exc: NotEntitled):
    """A feature the plan does not cover is a sentence and a link, not a stack trace."""
    return templates.TemplateResponse(
        request,
        "app/not_entitled.html",
        {"title": "Not on your plan", "message": str(exc), "capability": exc.capability},
        status_code=402,
    )


@app.exception_handler(StarletteHTTPException)
async def redirect_to_login(request: Request, exc: StarletteHTTPException):
    """Auth failures send people to the login page instead of a JSON error."""
    location = (exc.headers or {}).get("Location")
    if exc.status_code == 303 and location:
        return RedirectResponse(location, status_code=303)
    return await http_exception_handler(request, exc)
