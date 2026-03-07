"""
HTTP exceptions for API layer.

These exceptions are used ONLY in API routers to return proper HTTP responses.
They should NOT be used in services or repositories.
"""

from typing import Self

from fastapi import HTTPException, status


class CustomHTTPException(HTTPException):
    """Base HTTP exception with context support."""

    def with_context(self, detail: str) -> Self:
        """
        Add context to an HTTP exception.

        Args:
            detail: Additional information about the error

        Returns:
            A new HTTPException with updated details
        """
        self.detail = detail
        return self


# Standard HTTP exceptions for API layer
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

BAD_REQUEST = CustomHTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="Invalid request parameters",
)

UNPROCESSABLE_ENTITY = CustomHTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail="The request data is invalid",
)

INTERNAL_SERVER_ERROR = CustomHTTPException(
    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    detail="An internal server error occurred",
)

SERVICE_UNAVAILABLE = CustomHTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Service temporarily unavailable",
)
