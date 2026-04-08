"""Unit tests for clarinet.settings."""

import pytest
from pydantic import SecretStr

from clarinet.settings import Settings


@pytest.fixture
def settings_with_password(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Build a fresh Settings with a known password.

    `Settings.settings_customise_sources` drops `init_settings`, so kwargs to
    the constructor are ignored — values must come from env/TOML.
    """
    monkeypatch.setenv("CLARINET_DATABASE_DRIVER", "postgresql+asyncpg")
    monkeypatch.setenv("CLARINET_DATABASE_PASSWORD", "hunter2")
    monkeypatch.setenv("CLARINET_DATABASE_USERNAME", "app")
    monkeypatch.setenv("CLARINET_DATABASE_HOST", "db")
    monkeypatch.setenv("CLARINET_DATABASE_PORT", "5432")
    monkeypatch.setenv("CLARINET_DATABASE_NAME", "clarinet")
    return Settings()


class TestDatabasePasswordSecret:
    """`database_password` must be a SecretStr so dump/repr never leak it."""

    def test_password_is_secret_str(self, settings_with_password: Settings) -> None:
        assert isinstance(settings_with_password.database_password, SecretStr)
        assert settings_with_password.database_password.get_secret_value() == "hunter2"

    def test_repr_masks_password(self, settings_with_password: Settings) -> None:
        assert "hunter2" not in repr(settings_with_password)

    def test_model_dump_masks_password(self, settings_with_password: Settings) -> None:
        assert "hunter2" not in str(settings_with_password.model_dump())

    def test_model_dump_json_masks_password(self, settings_with_password: Settings) -> None:
        assert "hunter2" not in settings_with_password.model_dump_json()

    def test_database_url_still_contains_real_password(
        self, settings_with_password: Settings
    ) -> None:
        """The constructed URL must use the real password — masking is for logs only."""
        assert "hunter2" in settings_with_password.database_url
