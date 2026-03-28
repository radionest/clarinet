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
from typing import Any, Literal, Self

from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)
from taskiq.acks import AcknowledgeType

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


class QueueConfig(BaseSettings):
    """Configuration for message queue requirements."""

    have_gpu: bool = False
    have_dicom: bool = False

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
        toml_file=["settings.toml", "settings.custom.toml"],
        env_file=".env",
        env_prefix="CLARINET_",
        extra="ignore",
    )

    # Server settings
    port: int = 8000
    host: str = "127.0.0.1"
    root_url: str = ""
    api_base_url: str = ""
    api_verify_ssl: bool = True
    debug: bool = False
    coerce_null_query_params: bool = True

    # Storage settings
    storage_path: str = str(Path.home() / "clarinet/data")
    anon_id_prefix: str = "CLARINET"

    @field_validator("storage_path")
    @classmethod
    def resolve_storage_path(cls, v: str) -> str:
        """Resolve relative storage_path to absolute so external tools get correct paths."""
        return str(Path(v).resolve())

    anon_names_list: str | None = None

    # Frontend settings
    frontend_enabled: bool = True

    # Slicer settings
    slicer_script_paths: list[str] = []
    slicer_port: int = 2016
    slicer_timeout: float = 10.0

    # PACS server settings (used by backend DICOM service for anonymization etc.)
    pacs_host: str = "localhost"
    pacs_port: int = 4242
    pacs_aet: str = "ORTHANC"

    # RabbitMQ settings
    rabbitmq_login: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_exchange: str = "clarinet"
    rabbitmq_management_port: int = 15672
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

    # DICOM settings (local node)
    dicom_aet: str = "CLARINET"
    dicom_port: int = 11112
    dicom_ip: str | None = None
    dicom_max_pdu: int = 16384
    dicom_max_concurrent_associations: int = 8
    dicom_retrieve_mode: Literal["c-get", "c-move"] = "c-get"
    dicom_cmove_timeout: float = 300.0  # seconds to wait for SCP to receive instances
    dicom_log_identifiers: bool = False

    # DICOMweb proxy settings
    dicomweb_enabled: bool = True
    dicomweb_cache_ttl_hours: int = 24
    dicomweb_cache_max_size_gb: float = 10.0
    dicomweb_memory_cache_ttl_minutes: int = 30
    dicomweb_memory_cache_max_entries: int = 200
    dicomweb_cache_cleanup_enabled: bool = True
    dicomweb_cache_cleanup_interval: int = 86400  # 24 hours in seconds
    dicomweb_disk_write_concurrency: int = 4  # Max concurrent background disk writes

    # OHIF viewer settings
    ohif_enabled: bool = True
    ohif_default_version: str = "3.12.0"

    @property
    def ohif_path(self) -> Path:
        """Path to OHIF Viewer runtime files."""
        return Path(self.storage_path) / "ohif"

    # Security settings
    secret_key: str = "insecure-change-this-key-in-production"  # For session signing

    # Role settings
    extra_roles: list[str] = []

    # Admin user settings
    admin_username: str = "admin"
    admin_email: str = "admin@clarinet.ru"
    admin_password: str | None = None  # Required in production
    admin_auto_create: bool = True  # Auto-create admin on initialization
    admin_require_strong_password: bool = False  # Enforce in production

    # Session settings (KISS - only essentials)
    cookie_name: str = "clarinet_session"
    session_expire_hours: int = 24
    session_sliding_refresh: bool = True  # Auto-extend on activity
    session_absolute_timeout_days: int = 30  # Maximum session age
    session_idle_timeout_minutes: int = 60  # Inactivity timeout

    # Cleanup service settings
    session_cleanup_enabled: bool = True
    session_cleanup_interval: int = 3600  # Run every hour (in seconds)
    session_cleanup_batch_size: int = 1000
    session_cleanup_retention_days: int = 30

    # Session security settings
    session_concurrent_limit: int = 5  # Max sessions per user (0 = unlimited)
    session_ip_check: bool = False  # Validate IP consistency
    session_secure_cookie: bool = True  # HTTPS only in production
    session_cache_ttl_seconds: int = 30  # In-memory session validation cache TTL

    # Anonymization settings
    anon_uid_salt: str = "clarinet-anon-salt-change-in-production"
    anon_save_to_disk: bool = True
    anon_send_to_pacs: bool = False
    anon_failure_threshold: float = 0.5  # Max allowed failure ratio (0.0-1.0)

    # C-GET retry settings
    dicom_cget_max_retries: int = 3
    dicom_cget_retry_backoff: float = 2.0

    # Series filter settings
    series_filter_excluded_modalities: list[str] = [
        "SR",
        "KO",
        "PR",
        "DOC",
        "RTDOSE",
        "RTPLAN",
        "RTSTRUCT",
        "REG",
        "FID",
        "RWV",
    ]
    series_filter_min_instance_count: int | None = None
    series_filter_unknown_modality_policy: str = "include"
    series_filter_on_import: bool = False

    # Config mode settings
    config_mode: Literal["toml", "python"] = "toml"
    config_tasks_path: str = "./tasks/"
    config_delete_orphans: bool = False

    # Config file locations (relative to config_tasks_path)
    config_record_types_file: str = "record_types.py"
    config_files_catalog_file: str = "files_catalog.py"
    config_context_hydrators_file: str = "context_hydrators.py"
    config_schema_hydrators_file: str = "hydrators.py"

    # RecordFlow settings
    recordflow_enabled: bool = False  # Enable RecordFlow workflow engine
    recordflow_paths: list[str] = []  # Directories containing *_flow.py files

    # Pipeline settings
    pipeline_enabled: bool = False  # Enable pipeline task queue
    pipeline_result_backend_url: str | None = None  # Redis URL for result backend (optional)
    pipeline_worker_prefetch: int = 10  # Max tasks prefetched per worker
    pipeline_default_timeout: int = 3600  # Default task timeout in seconds
    pipeline_retry_count: int = 3  # Max retries for failed tasks
    pipeline_retry_delay: int = 5  # Initial retry delay (seconds)
    pipeline_retry_max_delay: int = 120  # Max retry delay with backoff
    pipeline_ack_type: AcknowledgeType = AcknowledgeType.WHEN_EXECUTED

    # Template settings
    template_dir: str | None = None
    static_dir: str | None = None

    # Logging settings
    log_level: str = "INFO"
    log_to_file: bool = True
    log_dir: str | None = None  # If None, will use {storage_path}/logs
    log_rotation: str = "20 MB"
    log_retention: str = "1 week"
    log_format: str | None = None  # Use default if None
    log_console_level: str | None = None  # If None, uses log_level
    log_serialize: bool = True  # JSON format for file logs
    log_noisy_libraries: list[str] = ["pynetdicom"]  # Suppress console INFO/DEBUG from these

    def model_post_init(self, __context: Any) -> None:
        """Link debug flag to log_level when log_level is not explicitly set."""
        if self.debug and self.log_level == "INFO":
            self.log_level = "DEBUG"
        # Default console to INFO when debug makes file-level DEBUG
        if self.log_console_level is None and self.log_level == "DEBUG":
            self.log_console_level = "INFO"

    # Project branding
    project_name: str = "Clarinet"
    project_description: str = "Medical Imaging Framework"

    # Project customization
    project_path: Path | None = None
    project_static_path: Path | None = None

    @property
    def effective_api_base_url(self) -> str:
        """Base URL for internal API client connections.

        When behind a reverse proxy (e.g. nginx with TLS termination),
        set ``api_base_url`` to the external URL so that the internal
        HTTP client receives valid Secure cookies.
        """
        return self.api_base_url or f"http://{self.host}:{self.port}/api"

    @property
    def session_expire_seconds(self) -> int:
        """Get session expiration time in seconds."""
        return self.session_expire_hours * 3600

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,  # noqa: ARG003
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize the sources for settings.

        Priority order: environment variables, then TOML config files
        """
        return env_settings, dotenv_settings, TomlConfigSettingsSource(settings_cls)

    @property
    def queue_config(self) -> QueueConfig:
        """Get queue requirements configuration."""
        return QueueConfig(
            have_gpu=self.have_gpu,
            have_dicom=self.have_dicom,
        )

    @property
    def database_url(self) -> str:
        """Get the database URL for SQLAlchemy."""
        if self.database_driver == DatabaseDriver.SQLITE:
            return f"sqlite:///{self.database_name}.db"
        else:
            return f"{self.database_driver.value}://{self.database_username}:{self.database_password}@{self.database_host}:{self.database_port}/{self.database_name}"

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

    def get_log_dir(self) -> Path:
        """Get the log directory path.

        Returns:
            Path to the log directory. Uses log_dir if specified,
            otherwise creates logs directory in storage_path.
        """
        if self.log_dir:
            return Path(self.log_dir)
        return Path(self.storage_path) / "logs"

    @property
    def static_path(self) -> Path:
        """Path to built frontend static files."""
        return Path(__file__).parent / "static"

    @property
    def custom_static_path(self) -> Path | None:
        """Path to user custom static files."""
        custom_path = Path.cwd() / "clarinet_custom"
        return custom_path if custom_path.exists() else None

    @property
    def static_directories(self) -> list[Path]:
        """List of static directories in priority order."""
        dirs = []
        if self.custom_static_path:
            dirs.append(self.custom_static_path)
        if self.static_path.exists():
            dirs.append(self.static_path)
        return dirs


@lru_cache
def get_settings() -> Settings:
    """Get the settings instance, with caching.

    Returns:
        Cached Settings instance
    """
    return Settings()


# Create a global settings instance for easy imports
settings = get_settings()
