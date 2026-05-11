"""Unit tests for clarinet.settings."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

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


class TestGetWorkerLogFile:
    """Resolution rules for ``Settings.get_worker_log_file``."""

    @pytest.fixture
    def settings(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
        monkeypatch.setenv("CLARINET_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("CLARINET_LOG_TO_FILE", "true")
        monkeypatch.delenv("CLARINET_WORKER_LOG_FILE", raising=False)
        return Settings()

    def test_default(self, settings: Settings, tmp_path: Path) -> None:
        assert settings.get_worker_log_file() == tmp_path / "clarinet_worker.log"

    def test_relative_setting_resolved_inside_log_dir(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        settings.worker_log_file = "gpu.log"
        assert settings.get_worker_log_file() == tmp_path / "gpu.log"

    def test_absolute_setting_used_as_is(self, settings: Settings, tmp_path: Path) -> None:
        absolute = tmp_path.parent / "abs_worker.log"
        settings.worker_log_file = str(absolute)
        assert settings.get_worker_log_file() == absolute

    def test_override_wins_over_setting(self, settings: Settings, tmp_path: Path) -> None:
        settings.worker_log_file = "from_setting.log"
        result = settings.get_worker_log_file("override.log")
        assert result == tmp_path / "override.log"

    def test_returns_none_when_log_to_file_disabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CLARINET_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("CLARINET_LOG_TO_FILE", "false")
        monkeypatch.setenv("CLARINET_WORKER_LOG_FILE", "anything.log")
        s = Settings()
        assert s.get_worker_log_file() is None
        assert s.get_worker_log_file("override.log") is None

    def test_env_var_picked_up(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Env var CLARINET_WORKER_LOG_FILE auto-populates the field."""
        monkeypatch.setenv("CLARINET_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("CLARINET_LOG_TO_FILE", "true")
        monkeypatch.setenv("CLARINET_WORKER_LOG_FILE", "from_env.log")
        s = Settings()
        assert s.worker_log_file == "from_env.log"
        assert s.get_worker_log_file() == tmp_path / "from_env.log"


class TestAnonIdPrefixValidator:
    """Settings-level validation for ``anon_id_prefix``.

    Values come through env (CLARINET_ANON_ID_PREFIX) because
    ``settings_customise_sources`` drops ``init_settings`` — kwargs to
    ``Settings(...)`` are ignored.
    """

    def test_default_clarinet_is_valid(self) -> None:
        assert Settings().anon_id_prefix == "CLARINET"

    def test_empty_string_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARINET_ANON_ID_PREFIX", "")
        assert Settings().anon_id_prefix == ""

    def test_alphanumeric_with_separators_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARINET_ANON_ID_PREFIX", "NIR_LIVER-V2")
        assert Settings().anon_id_prefix == "NIR_LIVER-V2"

    def test_rejects_non_ascii(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARINET_ANON_ID_PREFIX", "кириллица")
        with pytest.raises(ValidationError, match="anon_id_prefix"):
            Settings()

    def test_rejects_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARINET_ANON_ID_PREFIX", "has space")
        with pytest.raises(ValidationError, match="anon_id_prefix"):
            Settings()

    def test_rejects_special_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARINET_ANON_ID_PREFIX", "bad.dot")
        with pytest.raises(ValidationError, match="anon_id_prefix"):
            Settings()

    def test_rejects_over_55_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARINET_ANON_ID_PREFIX", "A" * 56)
        with pytest.raises(ValidationError, match="too long"):
            Settings()

    def test_55_chars_exactly_is_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARINET_ANON_ID_PREFIX", "A" * 55)
        assert len(Settings().anon_id_prefix) == 55
