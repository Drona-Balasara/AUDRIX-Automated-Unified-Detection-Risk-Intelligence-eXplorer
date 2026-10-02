"""Centralized, typed application configuration.

All environment-sensitive values flow through :class:`Settings`, which is backed
by ``pydantic-settings``. Values are read from (in order of precedence) process
environment variables, then a local ``.env`` file, then the defaults declared
here. This keeps configuration in one place and makes the SQLite location and
CORS policy configurable without editing application code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve project-relative paths so the default database location is predictable
# regardless of the current working directory the server is launched from.
#   config.py -> core -> app -> backend
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "sat_sa.db"


class Settings(BaseSettings):
    """Application settings loaded from the environment with sensible defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SATSA_",
        extra="ignore",
    )

    # Identity / presentation. ``service_id`` is a stable machine identifier that
    # clients and the health endpoint can rely on; it is not a secret.
    app_name: str = "SAT-SA"
    app_descriptor: str = "Security Assessment & Supervisory Analytics"
    service_id: str = "sat-sa-api"
    version: str = "0.1.0"

    # Deployment environment. Kept as a plain string so the frontend and logs can
    # reflect it; CORS behaviour below is environment-aware.
    environment: str = "development"

    # Database. Defaults to a file-backed SQLite database inside the repo ``data``
    # directory. Override via SATSA_DATABASE_URL for other environments.
    database_url: str = Field(default=f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")

    # CORS: explicit local Vite dev-server origins by default. Tightened or
    # widened per environment through SATSA_CORS_ORIGINS (comma-separated).
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    log_level: str = "INFO"

    # Maximum accepted upload size for ingestion, in bytes. Enforced at the
    # application layer (streamed bounded read) regardless of client-declared
    # Content-Length. Default 25 MiB is ample for local synthetic datasets.
    max_upload_bytes: int = 25 * 1024 * 1024

    # Maximum number of per-row validation errors returned in an API response.
    # The full error count is always reported; detail is capped so a malformed
    # file cannot force an unbounded response body.
    max_reported_errors: int = 100

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Allow CORS origins to be provided as a comma-separated string.

        Environment variables are strings, so a value like
        ``http://a:5173,http://b:5173`` is split into a list here.
        """
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_development(self) -> bool:
        return self.environment.lower() in {"development", "dev", "local"}


@lru_cache
def get_settings() -> Settings:
    """Return a cached settings instance.

    Caching ensures the environment is parsed once per process while remaining
    easy to override in tests (clear the cache or set env vars before import).
    """
    return Settings()
