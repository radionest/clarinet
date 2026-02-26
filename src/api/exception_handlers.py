"""
Exception handlers for converting domain exceptions to HTTP responses.

This module maps domain exceptions to appropriate HTTP status codes
and response formats for the API layer using FastAPI decorators.
"""

from typing import TYPE_CHECKING

from fastapi import Request, status
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from fastapi import FastAPI


def setup_exception_handlers(app: FastAPI) -> None:
    """Setup exception handlers using decorators.

    This function registers exception handlers for domain exceptions,
    converting them to appropriate HTTP responses.

    Args:
        app: FastAPI application instance
    """
    # Import domain exceptions inside function to avoid circular imports
    from src.exceptions.domain import (
        AlreadyAnonymizedError,
        AnonymizationFailedError,
        AuthenticationError,
        AuthorizationError,
        BusinessRuleViolationError,
        DatabaseError,
        EntityAlreadyExistsError,
        EntityNotFoundError,
        InvalidCredentialsError,
        SlicerConnectionError,
        SlicerError,
        ValidationError,
    )

    @app.exception_handler(EntityNotFoundError)
    async def handle_entity_not_found(_: Request, exc: EntityNotFoundError) -> JSONResponse:
        """Convert EntityNotFoundError to 404 response."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) if str(exc) else "Resource not found"},
        )

    @app.exception_handler(EntityAlreadyExistsError)
    async def handle_entity_already_exists(
        _: Request, exc: EntityAlreadyExistsError
    ) -> JSONResponse:
        """Convert EntityAlreadyExistsError to 409 response."""
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc) if str(exc) else "Resource already exists"},
        )

    @app.exception_handler(AuthenticationError)
    async def handle_authentication_error(_: Request, exc: AuthenticationError) -> JSONResponse:
        """Convert AuthenticationError to 401 response."""
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc) if str(exc) else "Authentication failed"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(InvalidCredentialsError)
    async def handle_invalid_credentials(_: Request, exc: InvalidCredentialsError) -> JSONResponse:
        """Convert InvalidCredentialsError to 401 response."""
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(exc) if str(exc) else "Invalid credentials"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(AuthorizationError)
    async def handle_authorization_error(_: Request, exc: AuthorizationError) -> JSONResponse:
        """Convert AuthorizationError to 403 response."""
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": str(exc) if str(exc) else "Insufficient permissions"},
        )

    @app.exception_handler(ValidationError)
    async def handle_validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        """Convert ValidationError to 422 response."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc) if str(exc) else "Validation failed"},
        )

    @app.exception_handler(BusinessRuleViolationError)
    async def handle_business_rule_violation(
        _: Request, exc: BusinessRuleViolationError
    ) -> JSONResponse:
        """Convert BusinessRuleViolationError to 409 response."""
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc) if str(exc) else "Business rule violation"},
        )

    @app.exception_handler(DatabaseError)
    async def handle_database_error(_: Request, _exc: DatabaseError) -> JSONResponse:
        """Convert DatabaseError to 500 response."""
        # Don't expose internal database errors to clients
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Database operation failed"},
        )

    @app.exception_handler(SlicerConnectionError)
    async def handle_slicer_connection(_: Request, exc: SlicerConnectionError) -> JSONResponse:
        """Convert SlicerConnectionError to 502 response."""
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc) if str(exc) else "3D Slicer is not reachable"},
        )

    @app.exception_handler(SlicerError)
    async def handle_slicer_error(_: Request, exc: SlicerError) -> JSONResponse:
        """Convert SlicerError to 422 response."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc) if str(exc) else "Slicer operation failed"},
        )

    @app.exception_handler(AlreadyAnonymizedError)
    async def handle_already_anonymized(_: Request, exc: AlreadyAnonymizedError) -> JSONResponse:
        """Convert AlreadyAnonymizedError to 409 response."""
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(AnonymizationFailedError)
    async def handle_anonymization_failed(
        _: Request, exc: AnonymizationFailedError
    ) -> JSONResponse:
        """Convert AnonymizationFailedError to 500 response."""
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    @app.exception_handler(FileNotFoundError)
    async def handle_file_not_found(_: Request, exc: FileNotFoundError) -> JSONResponse:
        """Convert FileNotFoundError to 404 response."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc) if str(exc) else "Resource not found"},
        )
