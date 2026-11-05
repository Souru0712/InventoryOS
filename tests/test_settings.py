"""Settings ignore a developer's .env under test, and still read it everywhere else."""
from app.config import Settings


def _env_file_with_support_email(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SUPPORT_EMAIL=leaked@example.com\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)


def test_a_dotenv_file_is_ignored_under_test(tmp_path, monkeypatch):
    _env_file_with_support_email(tmp_path, monkeypatch)
    assert Settings().support_email == "support@localhost"


def test_a_dotenv_file_is_still_read_outside_tests(tmp_path, monkeypatch):
    _env_file_with_support_email(tmp_path, monkeypatch)
    monkeypatch.setenv("APP_ENV", "dev")
    assert Settings().support_email == "leaked@example.com"


def test_the_suite_does_not_inherit_the_developer_base_url():
    # A developer .env with an https base URL made session cookies Secure and broke 46 tests (audit T1).
    assert Settings().app_base_url == "http://localhost:8000"
