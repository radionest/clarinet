"""
Domain exceptions for business logic layer.

These exceptions are used in repositories and services to represent
business logic errors without coupling to HTTP status codes.
"""

from typing import ClassVar, Self
from uuid import UUID


class ClarinetError(Exception):
    """Base exception for all Clarinet-specific errors."""

    def with_context(self, detail: str) -> Self:
        """Add context information to the exception."""
        self.args = (detail,)
        return self


# Base domain exceptions
class EntityNotFoundError(ClarinetError):
    """Raised when an entity is not found in the database."""


class EntityAlreadyExistsError(ClarinetError):
    """Raised when trying to create an entity that already exists."""


class AuthenticationError(ClarinetError):
    """Raised when authentication fails."""


class AuthorizationError(ClarinetError):
    """Raised when user lacks required permissions."""


class ValidationError(ClarinetError):
    """Raised when data validation fails."""


class BusinessRuleViolationError(ClarinetError):
    """Raised when a business rule is violated."""


# User-specific exceptions
class UserNotFoundError(EntityNotFoundError):
    """Raised when a user is not found."""

    def __init__(self, user_id: UUID | None = None):
        self.user_id = user_id
        if user_id:
            super().__init__(f"User with ID '{user_id}' not found")
        else:
            super().__init__("User not found")


class UserAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a user that already exists."""

    def __init__(self, email: str) -> None:
        self.email = email
        super().__init__(f"User with email '{email}' already exists")


class InvalidCredentialsError(AuthenticationError):
    """Raised when login credentials are invalid."""

    def __init__(self) -> None:
        super().__init__("Invalid username or password")


class InsufficientPermissionsError(AuthorizationError):
    """Raised when user lacks required permissions."""

    def __init__(self, action: str | None = None) -> None:
        self.action = action
        if action:
            super().__init__(f"Insufficient permissions for action: {action}")
        else:
            super().__init__("Insufficient permissions")


# Role-specific exceptions
class RoleNotFoundError(EntityNotFoundError):
    """Raised when a role is not found."""

    def __init__(self, role_name: str):
        self.role_name = role_name
        super().__init__(f"Role '{role_name}' not found")


class RoleAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a role that already exists."""

    def __init__(self, role_name: str):
        self.role_name = role_name
        super().__init__(f"Role '{role_name}' already exists")


class UserAlreadyHasRoleError(BusinessRuleViolationError):
    """Raised when trying to assign a role that user already has."""

    def __init__(self, user_id: UUID, role_name: str):
        self.user_id = user_id
        self.role_name = role_name
        super().__init__(f"User '{user_id}' already has role '{role_name}'")


# Study/Patient exceptions
class PatientNotFoundError(EntityNotFoundError):
    """Raised when a patient is not found."""

    def __init__(self, patient_id: str) -> None:
        self.patient_id = patient_id
        super().__init__(f"Patient with ID '{patient_id}' not found")


class PatientAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a patient that already exists."""

    def __init__(self, patient_id: str):
        self.patient_id = patient_id
        super().__init__(f"Patient with ID '{patient_id}' already exists")


class StudyNotFoundError(EntityNotFoundError):
    """Raised when a study is not found."""

    def __init__(self, study_uid: str):
        self.study_uid = study_uid
        super().__init__(f"Study with UID '{study_uid}' not found")


class StudyAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a study that already exists."""

    def __init__(self, study_uid: str):
        self.study_uid = study_uid
        super().__init__(f"Study with UID '{study_uid}' already exists")


class SeriesNotFoundError(EntityNotFoundError):
    """Raised when a series is not found."""

    def __init__(self, series_uid: str):
        self.series_uid = series_uid
        super().__init__(f"Series with UID '{series_uid}' not found")


class SeriesAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a series that already exists."""

    def __init__(self, series_uid: str):
        self.series_uid = series_uid
        super().__init__(f"Series with UID '{series_uid}' already exists")


# Anonymization exceptions
class AlreadyAnonymizedError(BusinessRuleViolationError):
    """Raised when trying to anonymize an already anonymized entity."""

    def __init__(self, entity_type: str):
        self.entity_type = entity_type
        super().__init__(f"{entity_type} is already anonymized")


class AnonymizationFailedError(BusinessRuleViolationError):
    """Raised when anonymization fails."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Anonymization failed: {reason}")


# Record-specific exceptions (formerly Task)
class RecordNotFoundError(EntityNotFoundError):
    """Raised when a record is not found."""

    def __init__(self, record_id: int):
        self.record_id = record_id
        super().__init__(f"Record with ID {record_id} not found")


class RecordAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a record that already exists."""


class RecordTypeNotFoundError(EntityNotFoundError):
    """Raised when a record type is not found."""

    def __init__(self, type_id: int | str):
        self.type_id = type_id
        super().__init__(f"Record type with ID '{type_id}' not found")


class RecordTypeAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a record type that already exists."""

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Record type with name '{name}' already exists")


class RecordConstraintViolationError(BusinessRuleViolationError):
    """Raised when a record constraint is violated."""


class RecordLimitReachedError(RecordConstraintViolationError):
    """Raised when max_records limit is reached.

    Expected in concurrent flows where multiple triggers try to create
    the same record type. Engine can safely downgrade to WARNING.
    """

    error_code: ClassVar[str] = "RECORD_LIMIT_REACHED"


class RecordUniquePerUserError(RecordConstraintViolationError):
    """Raised when unique_per_user constraint is violated.

    Expected during auto-assign when user already has a record of this type.
    """

    error_code: ClassVar[str] = "UNIQUE_PER_USER"


# Report exceptions (custom SQL reports)
class ReportNotFoundError(EntityNotFoundError):
    """Raised when a report template name is not registered."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Report '{name}' not found")


class ReportQueryError(ClarinetError):
    """Raised when a custom SQL report fails to execute or times out."""


# Configuration errors
class ConfigurationError(ClarinetError):
    """Raised when there's a configuration problem."""


# Database errors
class DatabaseError(ClarinetError):
    """Raised when there's a database operation error."""


class DatabaseConnectionError(DatabaseError):
    """Raised when database connection fails."""


class DatabaseIntegrityError(DatabaseError):
    """Raised when database integrity constraint is violated."""


# Migration errors
class MigrationError(ClarinetError):
    """Raised when database migration fails."""


# Storage errors
class StorageError(ClarinetError):
    """Raised when file storage operation fails."""


class FileNotFoundError(StorageError):
    """Raised when a file is not found."""


class FileAlreadyExistsError(StorageError):
    """Raised when trying to create a file that already exists."""


# File schema errors
class FileSchemaError(ClarinetError):
    """Base exception for file schema errors."""


class FilePatternError(FileSchemaError):
    """Raised when a file pattern is invalid."""


class RequiredFileMissingError(FileSchemaError):
    """Raised when a required file is not found."""

    def __init__(self, file_name: str, pattern: str):
        self.file_name = file_name
        self.pattern = pattern
        super().__init__(f"Required file '{file_name}' not found (pattern: {pattern})")


# DICOM errors
class DicomError(ClarinetError):
    """Base exception for DICOM-related errors."""


class PacsError(DicomError):
    """Raised when PACS operation fails."""


class DicomFilterError(DicomError):
    """Raised when DICOM filtering fails."""


# Image processing errors
class ImageError(ClarinetError):
    """Base exception for image processing errors."""


class ImageReadError(ImageError):
    """Raised when reading an image fails."""


class ImageWriteError(ImageError):
    """Raised when writing an image fails."""


# Slicer errors
class SlicerError(ClarinetError):
    """Base exception for Slicer-related errors."""


class SlicerConnectionError(SlicerError):
    """Raised when connection to Slicer fails."""


class SlicerSegmentationError(SlicerError):
    """Raised when Slicer segmentation fails."""


class ScriptError(SlicerError):
    """Raised when Slicer script execution fails."""


class NoScriptError(ScriptError):
    """Raised when a requested script is not found."""


class ScriptArgumentError(ScriptError):
    """Raised when script arguments are invalid."""


# RecordFlow errors
class RecordFlowError(ClarinetError):
    """Base exception for RecordFlow workflow errors."""


class FlowDefinitionError(RecordFlowError):
    """Raised when flow definition is invalid.

    Examples: or_()/and_() called without if_(), invalid trigger status.
    """


class FlowConditionError(RecordFlowError):
    """Raised when a flow condition is invalid or evaluation fails.

    Examples: unknown operator, invalid comparison.
    """


class FlowContextError(RecordFlowError):
    """Raised when record context is missing or invalid.

    Examples: record not found in context, cannot access field in non-dict.
    """

    def __init__(self, record_name: str, detail: str | None = None):
        self.record_name = record_name
        self.detail = detail
        if detail:
            super().__init__(f"Context error for record '{record_name}': {detail}")
        else:
            super().__init__(f"Record '{record_name}' not found in context")


class FlowExecutionError(RecordFlowError):
    """Raised when flow action execution fails.

    Examples: failed to create record, failed to update status.
    """

    def __init__(self, action: str, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(f"Failed to execute action '{action}': {reason}")


# Pipeline errors
class PipelineError(ClarinetError):
    """Base exception for pipeline task queue errors."""


class PipelineStepError(PipelineError):
    """Raised when a pipeline step fails during execution.

    Examples: task function raised an exception, timeout exceeded.
    """

    def __init__(self, step_name: str, reason: str):
        self.step_name = step_name
        self.reason = reason
        super().__init__(f"Pipeline step '{step_name}' failed: {reason}")


class PipelineConfigError(PipelineError):
    """Raised when pipeline configuration is invalid.

    Examples: unknown pipeline name, invalid queue, missing broker.
    """
