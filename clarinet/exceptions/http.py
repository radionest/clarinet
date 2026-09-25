"""
HTTP exceptions for API layer.

These exceptions are used ONLY in API routers to return proper HTTP responses.
They should NOT be used in services or repositories.

The constants below are process-wide templates shared by concurrent requests:
always raise ``X.with_context(...)``, never the bare constant, since raising
writes ``__traceback__``/``__context__`` onto the object being raised.
"""

from typing import Self

from fastapi import HTTPException, status


class CustomHTTPException(HTTPException):
    """Base HTTP exception with context support."""

    def with_context(self, detail: str) -> Self:
        """
        Return a copy of this exception with a new detail; ``self`` is untouched.

        Args:
            detail: Additional information about the error

        Returns:
            A new exception with the same status code and headers.
        """
        headers = dict(self.headers) if self.headers else None
        return type(self)(status_code=self.status_code, detail=detail, headers=headers)


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
    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
