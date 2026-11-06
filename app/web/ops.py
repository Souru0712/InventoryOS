"""Liveness and readiness: for the platform's health checks, not for people."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db import ping, schema_revisions
from app.storage import get_storage

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/healthz")
def healthz():
    """The process is up. It checks nothing else, so a database blip never gets it restarted."""
    return {"status": "ok", "env": get_settings().app_env}


@router.get("/readyz")
def readyz():
    """The process can serve real traffic: database reachable, schema current, uploads storable.

    Failures are named but not described; the detail goes to the logs, not to whoever asked.
    """
    checks = {"database": _database(), "schema": _schema(), "storage": _storage()}
    ready = all(result in ("ok", "skipped") for result in checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "not ready", "checks": checks},
        status_code=200 if ready else 503,
    )


def _database() -> str:
    try:
        ping()
        return "ok"
    except Exception:
        log.exception("readiness: database unreachable")
        return "failed"


def _schema() -> str:
    if get_settings().app_env == "test":
        return "skipped"
    try:
        current, head = schema_revisions()
    except Exception:
        log.exception("readiness: could not read the schema revision")
        return "failed"
    if current != head:
        log.error("readiness: database is at revision %s, code expects %s", current, head)
        return "behind"
    return "ok"


def _storage() -> str:
    try:
        get_storage().check()
        return "ok"
    except Exception:
        log.exception("readiness: upload storage unusable")
        return "failed"
