"""Response schemas for admin dashboard endpoints."""

from pydantic import BaseModel as PydanticBaseModel


class AdminStats(PydanticBaseModel):
    """Aggregate system statistics for admin dashboard."""

    total_studies: int
    total_records: int
    total_users: int
    total_patients: int
    records_by_status: dict[str, int]


class RecordTypeStatusCounts(PydanticBaseModel):
    """Per-status record counts for a record type."""

    blocked: int = 0
    pending: int = 0
    inwork: int = 0
    finished: int = 0
    failed: int = 0
    pause: int = 0


class RecordTypeStats(PydanticBaseModel):
    """Record type with aggregate statistics."""

    name: str
    description: str | None = None
    label: str | None = None
    level: str
    role_name: str | None = None
    min_records: int | None = None
    max_records: int | None = None
    total_records: int
    records_by_status: RecordTypeStatusCounts
    unique_users: int


class UserRoleInfo(PydanticBaseModel):
    """User info with role assignments for the role matrix."""

    id: str
    email: str
    is_active: bool
    is_superuser: bool
    role_names: list[str]


class RoleMatrixResponse(PydanticBaseModel):
    """Role matrix: all roles and all users with their assignments."""

    roles: list[str]
    users: list[UserRoleInfo]


class ClearOutputFilesResult(PydanticBaseModel):
    """Result of clearing output files from disk for a record."""

    deleted_files: list[str]
    deleted_links: int


class DeleteRecordResult(PydanticBaseModel):
    """Result of cascade-deleting a record with its descendants and output files."""

    deleted_ids: list[int]
    files_removed: int
