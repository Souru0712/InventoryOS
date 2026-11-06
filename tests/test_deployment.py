"""Deployed settings, readiness, error reporting and scheduler uniqueness."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, require_deployable
from app.db import SCHEDULER_LOCK, Base, LeaderLock, engine, is_postgres
from app.main import app
from app.models import EmailLog
from app.monitoring import FILTERED, init_monitoring, scrub
from app.orchestration import scheduler
from app.storage import StorageError

GOOD_PRODUCTION = dict(
    app_env="production",
    database_url="postgresql+psycopg://stockpulse:pw@db.internal:5432/stockpulse",
    secret_key="x" * 48,
    encryption_key="a-fernet-key",
    app_base_url="https://app.stockpulse.test",
)


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- settings ------------------------------------------------------------------------------------


@pytest.mark.parametrize("given", ["postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"])
def test_a_hosts_postgres_url_gets_the_installed_driver(given):
    assert settings(database_url=given).database_url == "postgresql+psycopg://u:p@h:5432/db"


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_env_var_that_exists_but_is_empty_means_unset(blank):
    """Hosts hand out empty strings for variables nobody filled in; that must not refuse to boot."""
    assert settings(run_scheduler_in_web=blank, app_env="dev").embedded_scheduler is True
    assert settings(max_upload_mb=blank).max_upload_mb == 15
    assert settings(storage_backend=blank).storage_backend == "local"


def test_a_sqlite_url_is_left_alone():
    assert (
        settings(database_url="sqlite:///./stockpulse.db").database_url
        == "sqlite:///./stockpulse.db"
    )


def test_development_is_never_refused():
    assert settings(app_env="dev").deployment_problems() == []


def test_a_properly_configured_production_starts():
    assert settings(**GOOD_PRODUCTION).deployment_problems() == []


def test_production_refuses_sqlite_placeholder_secrets_missing_keys_and_plain_http():
    problems = " ".join(
        settings(
            app_env="production",
            database_url="sqlite:///./stockpulse.db",
            secret_key="change-me-openssl-rand-hex-32",
            encryption_key="",
            app_base_url="http://app.stockpulse.test",
        ).deployment_problems()
    )
    for expected in ("SQLite", "SECRET_KEY", "ENCRYPTION_KEY", "https"):
        assert expected in problems


def test_staging_may_run_over_plain_http_for_a_local_replica():
    assert (
        settings(
            **{**GOOD_PRODUCTION, "app_env": "staging", "app_base_url": "http://localhost:8080"}
        ).deployment_problems()
        == []
    )


def test_s3_storage_needs_a_bucket():
    problems = settings(**GOOD_PRODUCTION, storage_backend="s3").deployment_problems()
    assert any("S3_BUCKET" in problem for problem in problems)


def test_refusing_to_start_names_every_problem():
    with pytest.raises(RuntimeError, match="SQLite.*ENCRYPTION_KEY"):
        require_deployable(
            settings(**{**GOOD_PRODUCTION, "database_url": "sqlite:///x.db", "encryption_key": ""})
        )


@pytest.mark.parametrize(
    "env, embedded", [("dev", True), ("test", False), ("staging", False), ("production", False)]
)
def test_the_web_process_runs_the_scheduler_only_in_development(env, embedded):
    assert settings(app_env=env).embedded_scheduler is embedded


def test_the_embedded_scheduler_can_be_forced_either_way():
    assert settings(app_env="production", run_scheduler_in_web=True).embedded_scheduler is True
    assert settings(app_env="dev", run_scheduler_in_web=False).embedded_scheduler is False


# --- error reporting -----------------------------------------------------------------------------


def test_error_reports_never_carry_cookies_auth_tokens_or_form_bodies():
    event = {
        "request": {
            "cookies": {"session": "abc"},
            "data": {"password": "a-good-password"},
            "query_string": "token=customer-login-secret",
            "headers": {
                "Authorization": "Bearer x",
                "Cookie": "session=abc",
                "Stripe-Signature": "t=1,v1=x",
                "User-Agent": "Safari",
            },
        }
    }
    request = scrub(event)["request"]
    assert "cookies" not in request and "data" not in request
    assert request["query_string"] == FILTERED
    assert request["headers"]["Authorization"] == FILTERED
    assert request["headers"]["Cookie"] == FILTERED
    assert request["headers"]["Stripe-Signature"] == FILTERED
    assert request["headers"]["User-Agent"] == "Safari"


def test_error_reporting_stays_off_without_a_dsn():
    assert init_monitoring("web") is False


# --- health checks -------------------------------------------------------------------------------


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def test_liveness_checks_nothing_but_the_process(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_checks_the_database_and_storage(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "schema": "skipped", "storage": "ok"},
    }


def test_readiness_fails_without_leaking_why(client, monkeypatch):
    class Broken:
        def check(self):
            raise StorageError("bucket secret-internal-bucket-name refused the credentials")

    monkeypatch.setattr("app.web.ops.get_storage", lambda: Broken())
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["storage"] == "failed"
    assert "secret-internal-bucket-name" not in response.text


# --- scheduled jobs ------------------------------------------------------------------------------


def test_a_rerun_report_job_does_not_send_the_weekly_report_twice(db, tenant, monkeypatch):
    now = datetime.now(UTC)
    tenant.report_day, tenant.report_hour = now.weekday(), now.hour
    db.commit()

    sent = []

    def fake_send(session, org):
        sent.append(org.tenant_id)
        session.add(
            EmailLog(
                tenant_id=org.tenant_id,
                to_email="owner@rosewood.test",
                subject="Your week",
                kind="weekly_report",
                status="sent",
            )
        )

    monkeypatch.setattr(scheduler, "send_weekly_report", fake_send)
    scheduler.send_due_reports(now)
    scheduler.send_due_reports(now)
    assert sent == [tenant.id]


@pytest.mark.skipif(not is_postgres(), reason="advisory locks exist only on Postgres")
def test_only_one_scheduler_holds_the_lock_and_a_standby_takes_over():
    leader, standby = LeaderLock(SCHEDULER_LOCK), LeaderLock(SCHEDULER_LOCK)
    try:
        assert leader.acquire() is True
        assert standby.acquire() is False
        assert leader.alive() is True
        leader.release()
        assert standby.acquire() is True
    finally:
        leader.release()
        standby.release()
