"""Test helpers for accounts: confirming an address the way the emailed link would (T1.08)."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User


def confirm_email(email: str) -> None:
    """Mark an account's address confirmed, so gated actions (uploads, POS) are open to it."""
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == email.strip().lower())).one()
        user.email_verified_at = datetime.now(UTC)
        db.commit()
