"""T1.10: invitations and team management (STF-FR-01, -06, -07; SAAS-A-08; DR-27)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Invitation, Location, Membership, Tenant, User, UserSession
from app.transform.team import TeamRuleError, check_change, check_grant, scope_for
from tests.accounts import confirm_email

OWNER = "owner@corner.test"
PASSWORD = "a-good-password"
NEW_PASSWORD = "another-good-password"


@pytest.fixture
def mailbox(monkeypatch):
    sent: list[dict] = []
    import app.team

    original = app.team.send_email

    def capture(db, tenant_id, to_email, subject, html, kind, text):
        sent.append({"to": to_email, "kind": kind, "subject": subject, "text": text})
        return original(db, tenant_id, to_email, subject, html, kind, text)

    monkeypatch.setattr("app.team.send_email", capture)
    return sent


def signup(business: str, email: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    client.post("/signup", data={"business_name": business, "email": email, "password": PASSWORD})
    confirm_email(email)
    return client


@pytest.fixture
def corner(mailbox):
    """Corner Cafe with two locations and its owner signed in."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    owner = signup("Corner Cafe", OWNER)
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant).where(Tenant.name == "Corner Cafe")).one()
        for name in ("Main St", "Harbor"):
            db.add(Location(tenant_id=tenant.id, name=name, timezone="America/New_York"))
        db.commit()
    clients = [owner]
    yield owner, clients
    for client in clients:
        client.__exit__(None, None, None)


def visitor(clients: list) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    clients.append(client)
    return client


def invite(client: TestClient, email: str, role: str = "employee", **extra):
    return client.post("/app/team/invite", data={"email": email, "role": role, **extra})


def token_in(mail: dict) -> str:
    return mail["text"].split("token=", 1)[1].split()[0]


def membership_of(email: str, business: str = "Corner Cafe") -> Membership | None:
    with SessionLocal() as db:
        return db.scalars(
            select(Membership)
            .join(User, User.id == Membership.user_id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(User.email == email, Tenant.name == business)
        ).first()


def location_id(name: str) -> int:
    with SessionLocal() as db:
        return db.scalars(select(Location.id).where(Location.name == name)).one()


def join_as_new(clients, mail: dict) -> TestClient:
    newcomer = visitor(clients)
    newcomer.post("/invitations/accept", data={"token": token_in(mail), "password": NEW_PASSWORD})
    return newcomer


# --- AC-STF-01: an emailed invitation, 7 days, single use, bound to the address ------------------


def test_an_invitation_creates_a_membership_with_the_chosen_role_once(corner, mailbox):
    owner, clients = corner
    page = invite(owner, "Sam@Example.test", "manager")
    assert "Invitation sent" in page.text
    (mail,) = mailbox
    assert mail["to"] == "sam@example.test" and mail["kind"] == "invitation"
    assert "Corner Cafe" in mail["subject"]
    token = token_in(mail)

    newcomer = visitor(clients)
    opened = newcomer.get("/invitations/accept", params={"token": token})
    assert "Join Corner Cafe" in opened.text and "Manager" in opened.text
    joined = newcomer.post(
        "/invitations/accept",
        data={"token": token, "password": "another-good-password"},
        follow_redirects=False,
    )
    assert joined.status_code == 303 and joined.headers["location"] == "/app?joined=1"
    assert newcomer.get("/app").status_code == 200

    membership = membership_of("sam@example.test")
    assert membership.role == "manager" and membership.status == "active"
    with SessionLocal() as db:
        sam = db.scalars(select(User).where(User.email == "sam@example.test")).one()
        assert sam.email_verified_at is not None  # the link reached the address
        stored = db.scalars(select(Invitation)).one()
        assert token not in stored.token_hash and stored.accepted_at is not None

    again = visitor(clients).post(
        "/invitations/accept", data={"token": token, "password": "x" * 12}
    )
    assert again.status_code == 400 and "expired" in again.text


def test_an_invitation_expires_after_seven_days(corner, mailbox):
    owner, clients = corner
    invite(owner, "sam@example.test")
    with SessionLocal() as db:
        row = db.scalars(select(Invitation)).one()
        assert row.expires_at - row.created_at == timedelta(days=7)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    newcomer = visitor(clients)
    token = token_in(mailbox[0])
    assert "expired" in newcomer.get("/invitations/accept", params={"token": token}).text
    refused = newcomer.post("/invitations/accept", data={"token": token, "password": "x" * 12})
    assert refused.status_code == 400
    assert membership_of("sam@example.test") is None


def test_only_the_invited_address_can_use_the_link(corner, mailbox):
    owner, clients = corner
    invite(owner, "sam@example.test")
    stranger = signup("Elsewhere", "someone.else@example.test")
    clients.append(stranger)
    token = token_in(mailbox[0])
    assert (
        "signed in with another account"
        in stranger.get("/invitations/accept", params={"token": token}).text
    )
    refused = stranger.post("/invitations/accept", data={"token": token})
    assert refused.status_code == 403
    assert membership_of("someone.else@example.test") is None
    with SessionLocal() as db:
        assert db.scalars(select(Invitation)).one().accepted_at is None


def test_someone_with_an_account_joins_with_their_password_and_keeps_their_business(
    corner, mailbox
):
    owner, clients = corner
    elsewhere = signup("Bakery", "baker@example.test")
    elsewhere.post("/logout")
    clients.append(elsewhere)
    invite(owner, "baker@example.test", "administrator")
    token = token_in(mailbox[0])
    page = elsewhere.get("/invitations/accept", params={"token": token})
    assert "Your StockPulse password" in page.text

    wrong = elsewhere.post(
        "/invitations/accept", data={"token": token, "password": "nope-nope-nope"}
    )
    assert wrong.status_code == 401 and membership_of("baker@example.test") is None

    joined = elsewhere.post(
        "/invitations/accept", data={"token": token, "password": PASSWORD}, follow_redirects=False
    )
    assert joined.status_code == 303
    assert membership_of("baker@example.test").role == "administrator"
    assert membership_of("baker@example.test", "Bakery").status == "active"
    # This browser now acts for the business it just joined.
    assert elsewhere.get("/app/team").status_code == 200


def test_a_signed_in_invitee_joins_with_one_click(corner, mailbox):
    owner, clients = corner
    baker = signup("Bakery", "baker@example.test")
    clients.append(baker)
    invite(owner, "baker@example.test")
    joined = baker.post(
        "/invitations/accept", data={"token": token_in(mailbox[0])}, follow_redirects=False
    )
    assert joined.status_code == 303
    assert membership_of("baker@example.test").role == "employee"
    assert "Corner Cafe" in baker.get("/app").text


def test_an_invitation_can_be_limited_to_some_locations(corner, mailbox):
    owner, clients = corner
    harbor = location_id("Harbor")
    invite(owner, "sam@example.test", "employee", scope="some", location_ids=[harbor])
    join_as_new(clients, mailbox[0])
    membership = membership_of("sam@example.test")
    with SessionLocal() as db:
        membership = db.get(Membership, membership.id)
        assert not membership.all_locations
        assert [link.location_id for link in membership.locations] == [harbor]


def test_inviting_again_replaces_the_earlier_link_and_withdrawing_ends_it(corner, mailbox):
    owner, clients = corner
    invite(owner, "sam@example.test")
    invite(owner, "sam@example.test", "read_only")
    first, second = token_in(mailbox[0]), token_in(mailbox[1])
    newcomer = visitor(clients)
    assert "expired" in newcomer.get("/invitations/accept", params={"token": first}).text
    with SessionLocal() as db:
        live = db.scalars(select(Invitation).where(Invitation.revoked_at.is_(None))).one()
    owner.post(f"/app/team/invitations/{live.id}/withdraw")
    assert "expired" in newcomer.get("/invitations/accept", params={"token": second}).text


def test_an_existing_member_is_not_invited_again(corner, mailbox):
    owner, _clients = corner
    page = invite(owner, OWNER)
    assert "already on the team" in page.text
    assert mailbox == []


def test_inviting_waits_for_a_confirmed_address(corner, mailbox):
    _owner, clients = corner
    with SessionLocal() as db:
        db.scalars(select(User).where(User.email == OWNER)).one().email_verified_at = None
        db.commit()
    owner = visitor(clients)
    owner.post("/login", data={"email": OWNER, "password": PASSWORD})
    assert invite(owner, "sam@example.test").status_code == 403
    assert mailbox == []


def test_one_person_cannot_send_endless_invitations(corner, mailbox):
    owner, _clients = corner
    for n in range(20):
        invite(owner, f"person{n}@example.test")
    page = invite(owner, "one.more@example.test")
    assert "a lot of invitations" in page.text
    assert len(mailbox) == 20


def test_guessing_at_invitation_links_is_stopped(corner, mailbox):
    _owner, clients = corner
    guesser = TestClient(app, client=("198.51.100.60", 50000))
    guesser.__enter__()
    clients.append(guesser)
    codes = [
        guesser.post("/invitations/accept", data={"token": f"guess-{n}"}).status_code
        for n in range(10)
    ]
    assert codes == [400] * 10
    assert guesser.post("/invitations/accept", data={"token": "guess"}).status_code == 429


# --- changing a role, removing a member (STF-FR-06, AC-STF-04) ---------------------------------


def test_a_new_role_applies_on_the_members_next_request(corner, mailbox):
    owner, clients = corner
    invite(owner, "sam@example.test")
    sam = join_as_new(clients, mailbox[0])
    assert sam.get("/app/team").status_code == 403
    membership = membership_of("sam@example.test")
    page = owner.post(f"/app/team/{membership.id}/role", data={"role": "administrator"})
    assert "Access updated" in page.text
    assert sam.get("/app/team").status_code == 200


def test_removing_a_member_ends_their_access_and_sessions_at_once(corner, mailbox):
    owner, clients = corner
    invite(owner, "sam@example.test")
    sam = join_as_new(clients, mailbox[0])
    assert sam.get("/app").status_code == 200
    membership = membership_of("sam@example.test")

    page = owner.post(f"/app/team/{membership.id}/remove")
    assert "Their access has ended" in page.text

    after = sam.get("/app", follow_redirects=False)
    assert after.status_code in (303, 403)
    assert membership_of("sam@example.test").status == "revoked"
    with SessionLocal() as db:
        sessions = db.scalars(
            select(UserSession).where(UserSession.user_id == membership.user_id)
        ).all()
        assert sessions and all(s.revoked_at is not None for s in sessions)


def test_a_removed_member_invited_back_gets_the_new_role(corner, mailbox):
    owner, clients = corner
    invite(owner, "sam@example.test", "manager")
    join_as_new(clients, mailbox[0])
    owner.post(f"/app/team/{membership_of('sam@example.test').id}/remove")
    invite(owner, "sam@example.test", "read_only")
    returning = visitor(clients)
    returning.post(
        "/invitations/accept",
        data={"token": token_in(mailbox[1]), "password": "another-good-password"},
    )
    membership = membership_of("sam@example.test")
    assert (membership.status, membership.role) == ("active", "read_only")


# --- AC-STF-05: the last active Owner, and who may touch an Owner --------------------------------


def test_the_last_owner_cannot_be_removed_or_demoted(corner, mailbox):
    owner, _clients = corner
    mine = membership_of(OWNER)
    demoted = owner.post(f"/app/team/{mine.id}/role", data={"role": "administrator"})
    removed = owner.post(f"/app/team/{mine.id}/remove")
    assert "always keeps at least one Owner" in demoted.text
    assert "always keeps at least one Owner" in removed.text
    membership = membership_of(OWNER)
    assert (membership.role, membership.status) == ("owner", "active")


def test_the_last_owner_rule_holds_whoever_asks():
    with pytest.raises(TeamRuleError, match="at least one Owner"):
        check_change(
            current_role="owner",
            new_role=None,
            is_self=False,
            can_manage_owners=True,
            active_owners=1,
        )
    check_change(
        current_role="owner",
        new_role="administrator",
        is_self=False,
        can_manage_owners=True,
        active_owners=2,
    )


def test_an_administrator_cannot_add_change_or_remove_an_owner(corner, mailbox):
    owner, clients = corner
    invite(owner, "admin@example.test", "administrator")
    admin = join_as_new(clients, mailbox[0])
    owner_membership = membership_of(OWNER)

    assert "Only an Owner can add" in invite(admin, "boss@example.test", "owner").text
    assert "Only an Owner can change" in admin.post(f"/app/team/{owner_membership.id}/remove").text
    assert (
        "Only an Owner can change"
        in admin.post(f"/app/team/{owner_membership.id}/role", data={"role": "employee"}).text
    )
    assert membership_of(OWNER).role == "owner" and len(mailbox) == 1
    assert 'value="owner"' not in admin.get("/app/team").text


def test_an_owner_can_add_a_second_owner_who_can_then_step_the_first_down(corner, mailbox):
    owner, clients = corner
    invite(owner, "partner@example.test", "owner")
    partner = join_as_new(clients, mailbox[0])
    page = partner.post(f"/app/team/{membership_of(OWNER).id}/role", data={"role": "administrator"})
    assert "Access updated" in page.text
    assert membership_of(OWNER).role == "administrator"


def test_nobody_changes_their_own_access_from_the_team_page(corner, mailbox):
    owner, clients = corner
    invite(owner, "admin@example.test", "administrator")
    admin = join_as_new(clients, mailbox[0])
    mine = membership_of("admin@example.test")
    assert "change your own access" in admin.post(f"/app/team/{mine.id}/remove").text
    assert membership_of("admin@example.test").status == "active"


def test_the_pure_rules():
    with pytest.raises(TeamRuleError):
        check_grant("owner", can_manage_owners=False)
    check_grant("owner", can_manage_owners=True)
    assert scope_for("administrator", False, frozenset({3})) == frozenset()
    assert scope_for("employee", False, frozenset({3})) == frozenset({3})
    with pytest.raises(TeamRuleError, match="at least one location"):
        scope_for("employee", False, frozenset())


def test_the_team_page_is_for_owners_and_administrators(corner, mailbox):
    owner, clients = corner
    invite(owner, "sam@example.test", "manager")
    sam = join_as_new(clients, mailbox[0])
    page = owner.get("/app/team")
    assert page.status_code == 200 and "sam@example.test" in page.text
    assert sam.get("/app/team").status_code == 403
