"""
Domain exceptions for business logic layer.

These exceptions are used in repositories and services to represent
business logic errors without coupling to HTTP status codes.

All exceptions inherit from ``ClarinetError`` which uses a metaclass
to auto-generate ``__init__`` from field annotations (similar to dataclasses).
All arguments are keyword-only.

Message resolution priority:
    1. ``format_message()`` method — for conditional/custom logic
    2. ``message_template`` ClassVar — auto-formatted from field values
    3. ``message`` field — direct message string (default fallback)
"""

from typing import ClassVar, Self
from uuid import UUID

from clarinet.exceptions.base import _ExceptionMeta


class ClarinetError(Exception, metaclass=_ExceptionMeta):
    """Base exception for all Clarinet-specific errors."""

    message: str = ""

    def with_context(self, detail: str) -> Self:
        """Add context information to the exception."""
        self.message = detail
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

    user_id: UUID | None = None

    def format_message(self) -> str:
        if self.user_id:
            return f"User with ID '{self.user_id}' not found"
        return "User not found"


class UserAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a user that already exists."""

    message_template: ClassVar[str] = "User with ID '{user_id}' already exists"
    user_id: UUID


class InvalidCredentialsError(AuthenticationError):
    """Raised when login credentials are invalid."""

    message: str = "Invalid username or password"


class InsufficientPermissionsError(AuthorizationError):
    """Raised when user lacks required permissions."""

    action: str | None = None

    def format_message(self) -> str:
        if self.action:
            return f"Insufficient permissions for action: {self.action}"
        return "Insufficient permissions"


# Role-specific exceptions
class RoleNotFoundError(EntityNotFoundError):
    """Raised when a role is not found."""

    message_template: ClassVar[str] = "Role '{role_name}' not found"
    role_name: str


class RoleAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a role that already exists."""

    message_template: ClassVar[str] = "Role '{role_name}' already exists"
    role_name: str


class UserAlreadyHasRoleError(BusinessRuleViolationError):
    """Raised when trying to assign a role that user already has."""

    message_template: ClassVar[str] = "User '{user_id}' already has role '{role_name}'"
    user_id: UUID
    role_name: str


# Study/Patient exceptions
class PatientNotFoundError(EntityNotFoundError):
    """Raised when a patient is not found."""

    message_template: ClassVar[str] = "Patient with ID '{patient_id}' not found"
    patient_id: str


class PatientAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a patient that already exists."""

    message_template: ClassVar[str] = "Patient with ID '{patient_id}' already exists"
    patient_id: str


class StudyNotFoundError(EntityNotFoundError):
    """Raised when a study is not found."""

    message_template: ClassVar[str] = "Study with UID '{study_uid}' not found"
    study_uid: str


class StudyAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a study that already exists."""

    message_template: ClassVar[str] = "Study with UID '{study_uid}' already exists"
    study_uid: str


class SeriesNotFoundError(EntityNotFoundError):
    """Raised when a series is not found."""

    message_template: ClassVar[str] = "Series with UID '{series_uid}' not found"
    series_uid: str


class SeriesAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a series that already exists."""

    message_template: ClassVar[str] = "Series with UID '{series_uid}' already exists"
    series_uid: str


# Anonymization exceptions
class AlreadyAnonymizedError(BusinessRuleViolationError):
    """Raised when trying to anonymize an already anonymized entity."""

    message_template: ClassVar[str] = "{entity_type} is already anonymized"
    entity_type: str


class AnonymizationFailedError(BusinessRuleViolationError):
    """Raised when anonymization fails."""

    message_template: ClassVar[str] = "Anonymization failed: {reason}"
    reason: str


# Record-specific exceptions (formerly Task)
class RecordNotFoundError(EntityNotFoundError):
    """Raised when a record is not found."""

    message_template: ClassVar[str] = "Record with ID {record_id} not found"
    record_id: int


class RecordAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a record that already exists."""


class RecordTypeNotFoundError(EntityNotFoundError):
    """Raised when a record type is not found."""

    message_template: ClassVar[str] = "Record type with ID '{type_id}' not found"
    type_id: int | str


class RecordTypeAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a record type that already exists."""

    message_template: ClassVar[str] = "Record type with name '{name}' already exists"
    name: str


class RecordConstraintViolationError(BusinessRuleViolationError):
    """Raised when a record constraint is violated."""


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

    message_template: ClassVar[str] = "Required file '{file_name}' not found (pattern: {pattern})"
    file_name: str
    pattern: str


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

    record_name: str
    detail: str | None = None

    def format_message(self) -> str:
        if self.detail:
            return f"Context error for record '{self.record_name}': {self.detail}"
        return f"Record '{self.record_name}' not found in context"


class FlowExecutionError(RecordFlowError):
    """Raised when flow action execution fails.

    Examples: failed to create record, failed to update status.
    """

    message_template: ClassVar[str] = "Failed to execute action '{action}': {reason}"
    action: str
    reason: str


# Pipeline errors
class PipelineError(ClarinetError):
    """Base exception for pipeline task queue errors."""


class PipelineStepError(PipelineError):
    """Raised when a pipeline step fails during execution.

    Examples: task function raised an exception, timeout exceeded.
    """

    message_template: ClassVar[str] = "Pipeline step '{step_name}' failed: {reason}"
    step_name: str
    reason: str


class PipelineConfigError(PipelineError):
    """Raised when pipeline configuration is invalid.

    Examples: unknown pipeline name, invalid queue, missing broker.
    """
