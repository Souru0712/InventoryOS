"""Request bodies read on the event loop, so the routes that use them can be plain `def` (C8).

A route declared `async def` runs on the event loop itself; the synchronous database, Anthropic,
Square and Stripe calls inside it then stop every other request on that worker until they
return. Plain `def` routes run in FastAPI's threadpool instead. Reading the body is the one step
that needs `await`; these dependencies do it before the route starts.
"""

from __future__ import annotations

from fastapi import Request
from starlette.datastructures import FormData


async def posted_form(request: Request) -> FormData:
    """The submitted form, for routes whose field names are not known in advance."""
    return await request.form()


async def raw_body(request: Request) -> bytes:
    """The exact bytes sent, for webhooks that verify a signature over them."""
    return await request.body()
