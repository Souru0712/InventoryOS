"""Every log line is JSON with a request id, and nothing personal reaches the logs (OBS-01, T0.10)."""

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app
from app.observability import JsonFormatter, configure_logging, log_context
from app.orchestration import scheduler

PASSWORD = "correct-horse-battery-7"
EMAIL = "owner.private@rosewood.test"


@pytest.fixture
def logs():
    """What the JSON handler would have written, line by line."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    previous = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    lines: list[dict] = []

    def read() -> list[dict]:
        lines[:] = [json.loads(line) for line in stream.getvalue().splitlines() if line]
        return lines

    yield read
    root.removeHandler(handler)
    root.setLevel(previous)


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def access_line(lines: list[dict], path: str) -> dict:
    return next(line for line in lines if line["logger"] == "app.access" and line["path"] == path)


def test_a_sign_in_leaves_no_password_or_email_in_the_logs(client, logs):
    client.post("/signup", data={"business_name": "Rosewood", "email": EMAIL, "password": PASSWORD})
    client.post("/logout")
    client.post("/login", data={"email": EMAIL, "password": PASSWORD + "-wrong"})
    client.post("/login", data={"email": EMAIL, "password": PASSWORD})

    lines = logs()
    raw = json.dumps(lines)
    assert lines, "requests were logged"
    assert PASSWORD not in raw
    assert EMAIL not in raw
    assert all(line.get("request_id") for line in lines)


def test_each_request_carries_its_id_in_the_response_and_in_its_lines(client, logs):
    response = client.get("/login")

    request_id = response.headers["x-request-id"]
    line = access_line(logs(), "/login")
    assert line["request_id"] == request_id
    assert (line["method"], line["status"]) == ("GET", 200)
    assert isinstance(line["duration_ms"], float)


def test_a_signed_in_request_names_its_tenant_and_user(client, logs):
    client.post("/signup", data={"business_name": "Rosewood", "email": EMAIL, "password": PASSWORD})

    client.get("/app")

    line = access_line(logs(), "/app")
    assert isinstance(line["tenant_id"], int) and isinstance(line["user_id"], int)


def test_a_callers_request_id_is_kept_and_a_strange_one_replaced(client):
    kept = client.get("/healthz", headers={"X-Request-ID": "upstream-1234abcd"})
    replaced = client.get("/healthz", headers={"X-Request-ID": '"><script>'})

    assert kept.headers["x-request-id"] == "upstream-1234abcd"
    assert replaced.headers["x-request-id"] != '"><script>'
    assert len(replaced.headers["x-request-id"]) == 32


def test_query_strings_never_reach_the_log(client, logs):
    """A customer's sign-in link carries its token in the query string."""
    client.get("/login?token=very-secret-token-value")

    assert "very-secret-token-value" not in json.dumps(logs())
    assert access_line(logs(), "/login")["path"] == "/login"


def test_an_email_address_in_a_message_is_masked(logs):
    with log_context():
        logging.getLogger("app.test").warning("could not reach %s", "someone@example.com")

    assert logs()[-1]["message"] == "could not reach [email]"


def test_each_job_run_has_its_own_id_and_names_the_tenant(logs):
    def nightly() -> None:
        logging.getLogger("app.test").info("starting")
        with log_context(tenant_id=7):
            logging.getLogger("app.test").info("tenant done")

    scheduler._job(nightly)()

    start, tenant = logs()[-2:]
    assert start["request_id"].startswith("job-") and start["job"] == "nightly"
    assert tenant["request_id"] == start["request_id"] and tenant["tenant_id"] == 7


def test_uvicorn_and_http_clients_keep_urls_out_of_the_log():
    root = logging.getLogger()
    saved = root.handlers[:]
    try:
        configure_logging()
        assert logging.getLogger("uvicorn.access").disabled
        assert logging.getLogger("httpx").getEffectiveLevel() == logging.WARNING
    finally:
        root.handlers = saved


def test_a_line_outside_any_request_still_has_an_id(logs):
    logging.getLogger("app.test").info("starting up")

    assert logs()[-1]["request_id"].startswith("process-")
