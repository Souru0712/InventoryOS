"""One-shot messages carried across a redirect in the signed session, never in the URL."""

from __future__ import annotations

from fastapi import Request


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message[:500], "kind": kind}


def pop_flash(request: Request) -> dict | None:
    return request.session.pop("flash", None)
