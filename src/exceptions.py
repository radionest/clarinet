"""
Exceptions for the Clarinet framework.

This module provides a set of standardized exceptions for use throughout the Clarinet framework.
Each exception is designed to be clear about what went wrong and provide useful context.
"""

from typing import Self
from fastapi import HTTPException, status


class ClarinetError(Exception):
    """Base exception for all Clarinet-specific errors."""

    pass


class ConfigError(ClarinetError):
    """Error related to configuration issues."""

    pass


class DatabaseError(ClarinetError):
    """Error related to database operations."""

    pass


class SecurityError(ClarinetError):
    """Error related to security, authentication, or authorization."""

    pass


class DicomError(ClarinetError):
    """Base exception for DICOM-related errors."""

    pass


class PacsError(DicomError):
    """Error related to PACS operations."""

    pass


class StorageError(DicomError):
    """Error related to file storage operations."""

    pass


class DicomFilterError(DicomError):
    """Error related to DICOM filtering operations."""

    pass


class AnonymizationError(DicomError):
    """Error related to DICOM anonymization operations."""

    pass


class ImageError(ClarinetError):
    """Base exception for image processing errors."""

    pass


class ImageReadError(ImageError):
    """Error related to reading image files."""

    pass


class ImageWriteError(ImageError):
    """Error related to writing image files."""

    pass


class SlicerError(ClarinetError):
    """Base exception for Slicer-related errors."""

    pass


class SlicerConnectionError(SlicerError):
    """Error related to connecting to Slicer."""

    pass


class ScriptError(SlicerError):
    """Error related to Slicer script execution."""

    pass


class NoScriptError(ScriptError):
    """Error indicating a requested script was not found."""

    pass


class ScriptArgumentError(ScriptError):
    """Error related to script arguments."""

    pass


class TaskError(ClarinetError):
    """Base exception for task-related errors."""

    pass


class TaskNotFoundError(TaskError):
    """Error indicating a task was not found."""

    pass


class TaskExistsError(TaskError):
    """Error indicating a task already exists."""

    pass


class TaskResultExistsError(TaskError):
    """Error indicating a task result already exists."""

    pass


class TaskTypeError(TaskError):
    """Error related to task types."""

    pass


class SeriesError(ClarinetError):
    """Base exception for series-related errors."""

    pass


class SeriesNotFoundError(SeriesError):
    """Error indicating a series was not found."""

    pass


class ValidationError(ClarinetError):
    """Error related to data validation."""

    pass


# Standard HTTP exceptions
class CustomHTTPException(HTTPException):
    def with_context(self, detail: str) -> Self:
        """
        Add context to an HTTP exception.

        Args:
            detail: information to add

        Returns:
            A new HTTPException with updated details
        """
        self.detail = detail
        return self


UNAUTHORIZED = CustomHTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid authentication credentials",
    headers={"WWW-Authenticate": "Bearer"},
)

FORBIDDEN = CustomHTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Not enough permissions to perform this action",
)

NOT_FOUND = CustomHTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="The requested resource was not found",
)

CONFLICT = CustomHTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="The request conflicts with the current state of the resource",
)

INTERNAL_SERVER_ERROR = CustomHTTPException(
    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    detail="An internal server error occurred",
)
