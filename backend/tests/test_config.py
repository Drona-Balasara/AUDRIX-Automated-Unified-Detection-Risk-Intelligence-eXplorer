"""Tests for centralized, typed configuration loading."""

from __future__ import annotations


def test_settings_defaults_load() -> None:
    from app.core.config import Settings

    settings = Settings()
    assert settings.service_id == "sat-sa-api"
    assert settings.app_name == "SAT-SA"
    assert settings.app_descriptor == "Security Assessment & Supervisory Analytics"
    assert settings.database_url.startswith("sqlite")
    assert isinstance(settings.cors_origins, list)
    assert settings.cors_origins  # non-empty default


def test_cors_origins_accepts_comma_separated_string() -> None:
    """A comma-separated env value is parsed into a list of origins."""
    from app.core.config import Settings

    settings = Settings(cors_origins="http://localhost:5173, http://127.0.0.1:5173")
    assert settings.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_get_settings_is_cached() -> None:
    from app.core.config import get_settings

    assert get_settings() is get_settings()
