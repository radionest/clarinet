"""
Configuration settings for Clarinet.

This module provides a settings class for Clarinet, with support for loading
configuration from TOML files and environment variables.
"""

import locale
import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

# Set locale for date/time formatting
try:
    if os.name == "nt":  # Windows
        locale.setlocale(locale.LC_TIME, "en-US")
    else:  # Unix/Linux
        locale.setlocale(locale.LC_TIME, "en_US.UTF-8")
except locale.Error:
    # Fallback if specified locale is not available
    pass


class DatabaseDriver(str, Enum):
    """Supported database drivers."""

    SQLITE = "sqlite"
    POSTGRESQL = "postgresql+psycopg2"
    POSTGRESQL_ASYNC = "postgresql+asyncpg"


class JWTAlgorithm(str, Enum):
    """Supported JWT encoding algorithms."""

    RS256 = "RS256"
    HS256 = "HS256"


class QueueConfig(BaseSettings):
    """Configuration for message queue requirements."""

    have_gpu: bool = False
    have_dicom: bool = False
    have_keras: bool = False
    have_torch: bool = False

    def has_not(self, conditions: Self) -> bool:
        """Check if the current configuration doesn't meet the specified conditions.

        Args:
            conditions: Another QueueConfig with requirements to check against

        Returns:
            True if any condition is not met, False otherwise
        """
        for cond_name, cond_value in conditions.model_dump(exclude_none=True).items():
            if getattr(self, cond_name) != cond_value:
                return True
        return False


class Settings(BaseSettings):
    """Main settings class for Clarinet.

    This class handles loading configuration from TOML files and environment variables,
    with support for custom settings sources.
    """

    model_config = SettingsConfigDict(
        toml_file=["settings.toml", "settings.custom.toml"], env_prefix="CLARINET_", extra="ignore"
    )

    # Server settings
    port: int = 8000
    host: str = "127.0.0.1"
    root_url: str = "/"
    debug: bool = False

    # Storage settings
    storage_path: str = str(Path.home() / "clarinet/data")
    anon_id_prefix: str = "CLARINET"
    anon_names_list: str | None = None

    # Slicer settings
    slicer_script_paths: list[str] = []

    # RabbitMQ settings
    rabbitmq_login: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_exchange: str = "clarinet"
    rabbitmq_max_consumers: int = 0

    # Queue requirements
    have_gpu: bool = False
    have_dicom: bool = False
    have_keras: bool = False
    have_torch: bool = False

    # Database settings
    database_driver: DatabaseDriver = DatabaseDriver.SQLITE
    database_host: str = "localhost"
    database_port: int = 5432
    database_name: str = "clarinet"
    database_username: str = "postgres"
    database_password: str = "postgres"

    # DICOM settings
    dicom_aet: str = "CLARINET"
    dicom_port: int = 11112
    dicom_ip: str | None = None

    # Security settings
    jwt_algorithm: JWTAlgorithm = JWTAlgorithm.HS256
    jwt_expire_minutes: int = 60
    jwt_secret_key: str = "insecure-change-this-key-in-production"

    # Template settings
    template_dir: str | None = None
    static_dir: str | None = None

    @classmethod
    def settings_customize_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize the sources for settings.

        Priority order: environment variables, then TOML config files
        """
        return env_settings, TomlConfigSettingsSource(settings_cls)

    @property
    def queue_config(self) -> QueueConfig:
        """Get queue requirements configuration."""
        return QueueConfig(
            have_gpu=self.have_gpu,
            have_dicom=self.have_dicom,
            have_keras=self.have_keras,
            have_torch=self.have_torch,
        )

    @property
    def database_url(self) -> str:
        """Get the database URL for SQLAlchemy."""
        if self.database_driver == DatabaseDriver.SQLITE:
            return f"sqlite:///{self.database_name}.db"
        else:
            return f"{self.database_driver}://{self.database_username}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"

    def get_template_dir(self) -> str:
        """Get the template directory path."""
        if self.template_dir:
            return self.template_dir
        return os.path.join(os.path.dirname(__file__), "..", "templates")

    def get_static_dir(self) -> str:
        """Get the static files directory path."""
        if self.static_dir:
            return self.static_dir
        return os.path.join(os.path.dirname(__file__), "..", "static")


@lru_cache
def get_settings() -> Settings:
    """Get the settings instance, with caching.

    Returns:
        Cached Settings instance
    """
    return Settings()


# Create a global settings instance for easy imports
settings = get_settings()
