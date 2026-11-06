"""Tenant isolation: the scope every query carries, and the helpers that apply it (C-33)."""

from app.access.context import OrgContext
from app.access.scoped import UnscopedAccess, get_scoped, select_scoped, update_scoped

__all__ = ["OrgContext", "UnscopedAccess", "get_scoped", "select_scoped", "update_scoped"]
