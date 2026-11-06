"""Runtime settings, env-driven."""

import os
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

DEPLOYED_ENVS = {"staging", "production"}
PLACEHOLDER_SECRET_KEYS = {"dev-insecure-secret-change-me", "change-me-openssl-rand-hex-32"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    app_base_url: str = "http://localhost:8000"
    # Development placeholder; production refuses it (see the validator below).
    secret_key: str = "dev-insecure-secret-change-me"  # noqa: S105
    encryption_key: str = ""
    # Attached to error reports, so a crash can be traced to the build that shipped it.
    release: str = ""

    database_url: str = "sqlite:///./stockpulse.db"

    # Development runs scheduled jobs inside the web process. Deployed, a dedicated
    # `python -m app.cli scheduler` process runs them, so more web workers never mean more jobs.
    run_scheduler_in_web: bool | None = None

    square_app_id: str = ""
    square_app_secret: str = ""
    square_env: str = "sandbox"
    square_api_version: str = "2025-01-23"
    square_webhook_signature_key: str = ""
    # C-34: in R1 nothing is written to Square. Write-back returns in R1.1 through the outbox;
    # until then the push functions in app/orchestration/sync.py refuse to run.
    square_write_back: bool = False

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "StockPulse <alerts@localhost>"
    support_email: str = "support@localhost"

    anthropic_api_key: str = ""
    extractor_model: str = "claude-opus-5"
    # SEC-13: uploads are at most 15 MB.
    max_upload_mb: int = 15

    # Where invoice photos live. "local" is a directory, which must be a persistent volume when
    # deployed; "s3" is any S3-compatible bucket (AWS, R2, Backblaze, Spaces, MinIO).
    storage_backend: str = "local"
    upload_dir: str = "var/uploads"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_prefix: str = "uploads"

    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_starter: str = ""
    stripe_price_pro: str = ""

    sync_interval_minutes: int = 60
    alert_cooldown_hours: int = 24
    expiry_horizon_days: int = 30
    customer_login_ttl_minutes: int = 20
    # Snack boxes are a private module, never part of the product. Empty means nobody can reach it.
    snack_box_tenant_ids: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Under test, whatever .env is on the developer's machine must not change the results.
        if os.environ.get("APP_ENV") == "test":
            return init_settings, env_settings, file_secret_settings
        return init_settings, env_settings, dotenv_settings, file_secret_settings

    @model_validator(mode="before")
    @classmethod
    def _blank_means_unset(cls, values):
        """An env var that exists but is empty is not a value.

        Hosts hand out empty strings for variables nobody filled in, and a blank `PORT` or
        `RUN_SCHEDULER_IN_WEB` should fall back to the default rather than refuse to boot.
        """
        if not isinstance(values, dict):
            return values
        return {
            key: value
            for key, value in values.items()
            if not (isinstance(value, str) and value.strip() == "")
        }

    @field_validator("database_url")
    @classmethod
    def _use_psycopg(cls, value: str) -> str:
        """Hosts hand out postgres:// URLs; SQLAlchemy has to be told which driver to use."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @property
    def square_api_base(self) -> str:
        return (
            "https://connect.squareupsandbox.com"
            if self.square_env == "sandbox"
            else "https://connect.squareup.com"
        )

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    @property
    def is_deployed(self) -> bool:
        return self.app_env in DEPLOYED_ENVS

    @property
    def embedded_scheduler(self) -> bool:
        if self.run_scheduler_in_web is not None:
            return self.run_scheduler_in_web
        return self.app_env == "dev"

    def deployment_problems(self) -> list[str]:
        """Settings that would lose data or weaken sessions if the app ran deployed with them."""
        if not self.is_deployed:
            return []
        problems = []
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL points at SQLite, which a redeploy would wipe")
        if self.secret_key in PLACEHOLDER_SECRET_KEYS or len(self.secret_key) < 32:
            problems.append("SECRET_KEY is a placeholder or shorter than 32 characters")
        if not self.encryption_key:
            problems.append("ENCRYPTION_KEY is not set, so Square tokens cannot be stored")
        if self.app_env == "production" and not self.app_base_url.startswith("https://"):
            problems.append(
                "APP_BASE_URL must be https in production, or session cookies travel in clear"
            )
        if self.storage_backend not in ("local", "s3"):
            problems.append(f"STORAGE_BACKEND {self.storage_backend!r} is neither local nor s3")
        if self.storage_backend == "s3" and not self.s3_bucket:
            problems.append("STORAGE_BACKEND is s3 but S3_BUCKET is not set")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_deployable(settings: Settings | None = None) -> None:
    """Refuse to start deployed with settings that would lose data or weaken sessions."""
    problems = (settings or get_settings()).deployment_problems()
    if problems:
        raise RuntimeError("refusing to start: " + "; ".join(problems))
