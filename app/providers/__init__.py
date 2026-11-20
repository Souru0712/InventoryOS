"""POS adapters by provider code (T3.01; TARGET_ARCHITECTURE §5).

Business code reaches a provider only through `adapter_for(code)` and the normalized types of
`app.providers.base`; each provider's package is imported here, lazily, and nowhere else outside
the integration seams (a test enforces it).
"""

from __future__ import annotations

from app.providers.base import PosAdapter, Registry

registry = Registry()


def _load() -> None:
    from app.providers.square.adapter import SquareAdapter

    registry.register(SquareAdapter())


def adapter_for(provider: str) -> PosAdapter:
    """The adapter for a provider code; `Unsupported` when there is none."""
    if not registry.adapters:
        _load()
    return registry.get(provider)


def providers() -> tuple[str, ...]:
    if not registry.adapters:
        _load()
    return registry.codes()
