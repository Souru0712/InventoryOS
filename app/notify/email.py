"""Email delivery over SMTP, logged per tenant. Falls back to logging in dev."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EmailLog, EmailSuppression, User

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# Each category is a thing a person can turn off. Anything not listed here was asked for by the
# person receiving it - a sign-in link, an order receipt - and is always sent.
CATEGORIES = ("stock_alerts", "expiry_alerts", "weekly_report")


def render(template: str, **context: object) -> str:
    return _env.get_template(template).render(**context)


def render_pair(stem: str, **context: object) -> tuple[str, str]:
    """Render one email in both parts. Every template has a .txt beside its .html."""
    return render(f"{stem}.html", **context), render(f"{stem}.txt", **context)


def wants(user: User, category: str) -> bool:
    """Whether this person still wants this kind of email."""
    return {
        "stock_alerts": user.wants_stock_alerts,
        "expiry_alerts": user.wants_expiry_alerts,
        "weekly_report": user.wants_weekly_report,
    }.get(category, True)


def suppression(db: Session, email: str) -> EmailSuppression | None:
    """The reason we stopped sending to an address, if we did."""
    return db.scalars(
        select(EmailSuppression).where(EmailSuppression.email == email.strip().lower())
    ).first()


def suppress(db: Session, email: str, reason: str, source: str = "bounce") -> EmailSuppression:
    """Stop sending to an address. Sending to one that bounces costs everyone deliverability."""
    address = email.strip().lower()
    existing = suppression(db, address)
    if existing:
        existing.source, existing.reason = source, reason[:300]
        db.flush()
        return existing
    entry = EmailSuppression(email=address, source=source, reason=reason[:300])
    db.add(entry)
    db.flush()
    log.warning("suppressed email to %s: %s", address, reason)
    return entry


def unsuppress(db: Session, email: str) -> bool:
    """Send to it again, once the address is fixed."""
    entry = suppression(db, email)
    if entry is None:
        return False
    db.delete(entry)
    db.flush()
    return True


def send_email(
    db: Session,
    tenant_id: int,
    to_email: str,
    subject: str,
    html: str,
    kind: str,
    text: str,
) -> bool:
    """Send one email and record the attempt. Returns True when it left the process.

    The plain-text part is not optional: a mail with a filler text part reads as spam to every
    filter that looks, and as nothing at all to whoever reads mail as text.
    """
    settings = get_settings()
    entry = EmailLog(tenant_id=tenant_id, to_email=to_email, subject=subject, kind=kind)

    blocked = suppression(db, to_email)
    if blocked is not None:
        entry.status = "suppressed"
        entry.error = f"{blocked.source}: {blocked.reason or 'address suppressed'}"[:300]
        db.add(entry)
        db.flush()
        log.info("not sending %r to a suppressed address", subject)
        return False

    if not settings.smtp_host:
        log.info("SMTP not configured; would send %r to %s", subject, to_email)
        entry.status = "skipped"
        entry.error = "SMTP_HOST is not configured"
        db.add(entry)
        db.flush()
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message["Reply-To"] = settings.support_email
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(message)
    except Exception as exc:
        entry.status = "failed"
        entry.error = f"{type(exc).__name__}: {exc}"
        db.add(entry)
        db.flush()
        log.exception("failed to send %r to %s", subject, to_email)
        raise

    db.add(entry)
    db.flush()
    return True
