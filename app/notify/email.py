"""Email delivery over SMTP, logged per tenant. Falls back to logging in dev."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import EmailLog

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(template: str, **context: object) -> str:
    return _env.get_template(template).render(**context)


def send_email(
    db: Session,
    tenant_id: int,
    to_email: str,
    subject: str,
    html: str,
    kind: str,
    text: str | None = None,
) -> bool:
    """Send one email and record the attempt. Returns True when it left the process."""
    settings = get_settings()
    entry = EmailLog(tenant_id=tenant_id, to_email=to_email, subject=subject, kind=kind)

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
    message.set_content(text or "This report is best viewed as HTML.")
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
