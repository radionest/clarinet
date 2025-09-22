"""
Domain exceptions for business logic layer.

These exceptions are used in repositories and services to represent
business logic errors without coupling to HTTP status codes.
"""

from typing import Self
from uuid import UUID


class ClarinetError(Exception):
    """Base exception for all Clarinet-specific errors."""

    def with_context(self, detail: str) -> Self:
        """Add context information to the exception.

        Args:
            detail: Additional details about the error

        Returns:
            Self with updated message
        """
        self.args = (detail,)
        return self


# Base domain exceptions
class EntityNotFoundError(ClarinetError):
    """Raised when an entity is not found in the database."""

    pass


class EntityAlreadyExistsError(ClarinetError):
    """Raised when trying to create an entity that already exists."""

    pass


class AuthenticationError(ClarinetError):
    """Raised when authentication fails."""

    pass


class AuthorizationError(ClarinetError):
    """Raised when user lacks required permissions."""

    pass


class ValidationError(ClarinetError):
    """Raised when data validation fails."""

    pass


class BusinessRuleViolationError(ClarinetError):
    """Raised when a business rule is violated."""

    pass


# User-specific exceptions
class UserNotFoundError(EntityNotFoundError):
    """Raised when a user is not found."""

    def __init__(self, user_id: UUID | None = None):
        if user_id:
            super().__init__(f"User with ID '{user_id}' not found")
        else:
            super().__init__("User not found")


class UserAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a user that already exists."""

    def __init__(self, user_id: UUID):
        super().__init__(f"User with ID '{user_id}' already exists")


class InvalidCredentialsError(AuthenticationError):
    """Raised when login credentials are invalid."""

    def __init__(self) -> None:
        super().__init__("Invalid username or password")


class InsufficientPermissionsError(AuthorizationError):
    """Raised when user lacks required permissions."""

    def __init__(self, action: str | None = None) -> None:
        if action:
            super().__init__(f"Insufficient permissions for action: {action}")
        else:
            super().__init__("Insufficient permissions")


# Role-specific exceptions
class RoleNotFoundError(EntityNotFoundError):
    """Raised when a role is not found."""

    def __init__(self, role_name: str):
        super().__init__(f"Role '{role_name}' not found")


class RoleAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a role that already exists."""

    def __init__(self, role_name: str):
        super().__init__(f"Role '{role_name}' already exists")


class UserAlreadyHasRoleError(BusinessRuleViolationError):
    """Raised when trying to assign a role that user already has."""

    def __init__(self, user_id: UUID, role_name: str):
        super().__init__(f"User '{user_id}' already has role '{role_name}'")


# Study/Patient exceptions
class PatientNotFoundError(EntityNotFoundError):
    """Raised when a patient is not found."""

    def __init__(self, patient_id: str) -> None:
        super().__init__(f"Patient with ID '{patient_id}' not found")


class PatientAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a patient that already exists."""

    def __init__(self, patient_id: str):
        super().__init__(f"Patient with ID '{patient_id}' already exists")


class StudyNotFoundError(EntityNotFoundError):
    """Raised when a study is not found."""

    def __init__(self, study_uid: str):
        super().__init__(f"Study with UID '{study_uid}' not found")


class StudyAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a study that already exists."""

    def __init__(self, study_uid: str):
        super().__init__(f"Study with UID '{study_uid}' already exists")


class SeriesNotFoundError(EntityNotFoundError):
    """Raised when a series is not found."""

    def __init__(self, series_uid: str):
        super().__init__(f"Series with UID '{series_uid}' not found")


class SeriesAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a series that already exists."""

    def __init__(self, series_uid: str):
        super().__init__(f"Series with UID '{series_uid}' already exists")


# Anonymization exceptions
class AlreadyAnonymizedError(BusinessRuleViolationError):
    """Raised when trying to anonymize an already anonymized entity."""

    def __init__(self, entity_type: str):
        super().__init__(f"{entity_type} is already anonymized")


class AnonymizationFailedError(BusinessRuleViolationError):
    """Raised when anonymization fails."""

    def __init__(self, reason: str):
        super().__init__(f"Anonymization failed: {reason}")


# Task-specific exceptions
class TaskNotFoundError(EntityNotFoundError):
    """Raised when a task is not found."""

    def __init__(self, task_id: int):
        super().__init__(f"Task with ID {task_id} not found")


class TaskAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a task that already exists."""

    pass


class TaskDesignNotFoundError(EntityNotFoundError):
    """Raised when a task design is not found."""

    def __init__(self, design_id: int | str):
        super().__init__(f"Task design with ID '{design_id}' not found")


class TaskDesignAlreadyExistsError(EntityAlreadyExistsError):
    """Raised when trying to create a task design that already exists."""

    def __init__(self, name: str):
        super().__init__(f"Task design with name '{name}' already exists")


class TaskConstraintViolationError(BusinessRuleViolationError):
    """Raised when a task constraint is violated."""

    pass


# Configuration errors
class ConfigurationError(ClarinetError):
    """Raised when there's a configuration problem."""

    pass


# Database errors
class DatabaseError(ClarinetError):
    """Raised when there's a database operation error."""

    pass


class DatabaseConnectionError(DatabaseError):
    """Raised when database connection fails."""

    pass


class DatabaseIntegrityError(DatabaseError):
    """Raised when database integrity constraint is violated."""

    pass


# Migration errors
class MigrationError(ClarinetError):
    """Raised when database migration fails."""

    pass


# Storage errors
class StorageError(ClarinetError):
    """Raised when file storage operation fails."""

    pass


class FileNotFoundError(StorageError):
    """Raised when a file is not found."""

    pass


class FileAlreadyExistsError(StorageError):
    """Raised when trying to create a file that already exists."""

    pass


# DICOM errors
class DicomError(ClarinetError):
    """Base exception for DICOM-related errors."""

    pass


class PacsError(DicomError):
    """Raised when PACS operation fails."""

    pass


class DicomFilterError(DicomError):
    """Raised when DICOM filtering fails."""

    pass


# Image processing errors
class ImageError(ClarinetError):
    """Base exception for image processing errors."""

    pass


class ImageReadError(ImageError):
    """Raised when reading an image fails."""

    pass


class ImageWriteError(ImageError):
    """Raised when writing an image fails."""

    pass


# Slicer errors
class SlicerError(ClarinetError):
    """Base exception for Slicer-related errors."""

    pass


class SlicerConnectionError(SlicerError):
    """Raised when connection to Slicer fails."""

    pass


class SlicerSegmentationError(SlicerError):
    """Raised when Slicer segmentation fails."""

    pass


class ScriptError(SlicerError):
    """Raised when Slicer script execution fails."""

    pass


class NoScriptError(ScriptError):
    """Raised when a requested script is not found."""

    pass


class ScriptArgumentError(ScriptError):
    """Raised when script arguments are invalid."""

    pass
