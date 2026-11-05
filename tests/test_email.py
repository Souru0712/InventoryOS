"""Email as a channel people actually receive: real text parts, their choices, and no repeats."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config import get_settings
from app.models import Alert, AlertKind, EmailLog, StockItem, TrackMode, User
from app.notify.email import suppress
from app.orchestration.alerts import send_alert_digest
from app.orchestration.reporting import send_weekly_report
from app.security import hash_password
from app.units import Dimension


class FakeSMTP:
    """Stands in for a mail server, and keeps what would have gone over the wire."""

    outbox: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def starttls(self):
        pass

    def login(self, _user, _password):
        pass

    def send_message(self, message):
        type(self).outbox.append(message)


@pytest.fixture
def mail(monkeypatch):
    """A configured mail server, so sending is a real send and not a log line."""
    FakeSMTP.outbox = []
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test", raising=False)
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    return FakeSMTP.outbox


def plain(message) -> str:
    return message.get_body(preferencelist=("plain",)).get_content()


def html_of(message) -> str:
    return message.get_body(preferencelist=("html",)).get_content()


@pytest.fixture
def milk(db, tenant, location) -> StockItem:
    item = StockItem(
        tenant_id=tenant.id,
        location_id=location.id,
        slug="whole-milk",
        name="Whole milk",
        dimension=Dimension.VOLUME,
        stock_unit="l",
        track_mode=TrackMode.MANUAL,
        qty_base=Decimal("4000"),
        reorder_point_base=Decimal("20000"),
        cost_per_base_cents=Decimal("0.095"),
    )
    db.add(item)
    db.commit()
    return item


def alert(db, tenant, item, kind: AlertKind, message: str, value_cents: int | None = None) -> Alert:
    row = Alert(
        tenant_id=tenant.id,
        stock_item_id=item.id,
        kind=kind,
        message=message,
        qty_base=Decimal("4000"),
        threshold_base=Decimal("20000"),
        value_cents=value_cents,
    )
    db.add(row)
    db.commit()
    return row


def second_user(db, tenant, email: str, **preferences) -> User:
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password("a-good-password"),
        **preferences,
    )
    db.add(user)
    db.commit()
    return user


# --- real text parts ------------------------------------------------------------------------


def test_the_alert_digest_is_readable_without_html(db, tenant, milk, mail):
    alert(db, tenant, milk, AlertKind.LOW_STOCK, "Whole milk is at 4 l, below its reorder point")
    assert send_alert_digest(db, tenant.id) is True

    text = plain(mail[0])
    assert "Whole milk is at 4 l" in text
    assert "best viewed as HTML" not in text
    assert "<" not in text.replace("<br>", ""), "the text part must not be markup"
    assert "Whole milk" in html_of(mail[0])


def test_the_weekly_report_is_readable_without_html(db, tenant, milk, mail):
    assert send_weekly_report(db, tenant.id) is True

    text = plain(mail[0])
    assert "Net sales" in text and "Stock on hand" in text
    assert "best viewed as HTML" not in text


def test_a_sign_in_link_is_readable_without_html(db, tenant, mail):
    from app.orchestration.customers import find_or_create, issue_login_link

    customer = find_or_create(db, tenant.id, "shopper@rosewood.test")
    db.commit()
    issue_login_link(db, customer)
    db.commit()

    text = plain(mail[0])
    assert "/shop/" in text and "expires in" in text


# --- what each person asked for ---------------------------------------------------------------


def test_someone_who_turned_the_weekly_report_off_is_not_sent_it(db, tenant, milk, mail):
    owner = db.query(User).one()
    owner.wants_weekly_report = False
    db.commit()

    assert send_weekly_report(db, tenant.id) is False
    assert mail == []


def test_each_person_gets_only_the_alert_categories_they_want(db, tenant, milk, mail):
    owner = db.query(User).one()
    owner.wants_expiry_alerts = False  # the owner watches stock levels only
    db.commit()
    second_user(db, tenant, "chef@rosewood.test", wants_stock_alerts=False)

    alert(db, tenant, milk, AlertKind.LOW_STOCK, "Whole milk is at 4 l, below its reorder point")
    alert(
        db,
        tenant,
        milk,
        AlertKind.WASTE_RISK,
        "6 l of Whole milk will not sell before 20 Sep",
        2400,
    )
    assert send_alert_digest(db, tenant.id) is True

    by_recipient = {message["To"]: plain(message) for message in mail}
    assert set(by_recipient) == {"owner@rosewood.test", "chef@rosewood.test"}
    assert "below its reorder point" in by_recipient["owner@rosewood.test"]
    assert "will not sell" not in by_recipient["owner@rosewood.test"]
    assert "will not sell" in by_recipient["chef@rosewood.test"]
    assert "below its reorder point" not in by_recipient["chef@rosewood.test"]


def test_nobody_wanting_a_category_means_nothing_is_sent(db, tenant, milk, mail):
    owner = db.query(User).one()
    owner.wants_stock_alerts = False
    db.commit()
    alert(db, tenant, milk, AlertKind.LOW_STOCK, "Whole milk is low")

    assert send_alert_digest(db, tenant.id) is False
    assert mail == []


# --- addresses we must stop writing to ---------------------------------------------------------


def test_a_bounced_address_is_never_written_to_again(db, tenant, milk, mail):
    suppress(db, "OWNER@rosewood.test", "550 mailbox does not exist")
    db.commit()

    assert send_weekly_report(db, tenant.id) is False
    assert mail == []
    logged = db.query(EmailLog).one()
    assert logged.status == "suppressed"
    assert "mailbox does not exist" in logged.error


def test_clearing_a_suppression_lets_mail_flow_again(db, tenant, milk, mail):
    from app.notify.email import unsuppress

    suppress(db, "owner@rosewood.test", "550 mailbox full")
    db.commit()
    send_weekly_report(db, tenant.id)
    assert unsuppress(db, "owner@rosewood.test") is True
    db.commit()

    assert send_weekly_report(db, tenant.id) is True
    assert len(mail) == 1


# --- repeats -----------------------------------------------------------------------------------


def test_a_retried_digest_does_not_email_the_same_alerts_twice(db, tenant, milk, mail):
    """The scheduler retrying, or two syncs in an hour, must not mean two digests."""
    alert(db, tenant, milk, AlertKind.LOW_STOCK, "Whole milk is at 4 l, below its reorder point")

    assert send_alert_digest(db, tenant.id) is True
    assert send_alert_digest(db, tenant.id) is False
    assert len(mail) == 1


def test_new_alerts_still_wait_for_the_cooldown_to_pass(db, tenant, milk, mail):
    first = alert(db, tenant, milk, AlertKind.LOW_STOCK, "Whole milk is low")
    send_alert_digest(db, tenant.id)
    alert(db, tenant, milk, AlertKind.OUT_OF_STOCK, "Whole milk is out")

    assert send_alert_digest(db, tenant.id) is False, "inside the cooldown window"

    first.notified_at = datetime.now(UTC) - timedelta(hours=get_settings().alert_cooldown_hours + 1)
    db.commit()
    assert send_alert_digest(db, tenant.id) is True
    assert len(mail) == 2
