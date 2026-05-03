"""
Exception handlers for converting domain exceptions to HTTP responses.

This module maps domain exceptions to appropriate HTTP status codes
and response formats for the API layer using FastAPI decorators.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from clarinet.utils.logger import logger


def setup_exception_handlers(app: FastAPI) -> None:
    """Setup exception handlers using decorators.

    This function registers exception handlers for domain exceptions,
    converting them to appropriate HTTP responses.

    Args:
        app: FastAPI application instance
    """
    # Import domain exceptions inside function to avoid circular imports
    from clarinet.exceptions.domain import (
        AlreadyAnonymizedError,
        AnonymizationFailedError,
        AuthenticationError,
        AuthorizationError,
        BusinessRuleViolationError,
        ConfigurationError,
        DatabaseError,
        EntityAlreadyExistsError,
        EntityNotFoundError,
        InvalidCredentialsError,
        PipelineError,
        RecordLimitReachedError,
        RecordUniquePerUserError,
        ReportQueryError,
        SlicerConnectionError,
        SlicerError,
        ValidationError,
    )
    from clarinet.utils.pagination import InvalidCursorError

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

    @app.exception_handler(InvalidCursorError)
    async def handle_invalid_cursor(_: Request, exc: InvalidCursorError) -> JSONResponse:
        """Convert InvalidCursorError to 400 response (malformed opaque token)."""
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc) if str(exc) else "Invalid pagination cursor"},
        )

    @app.exception_handler(ValidationError)
    async def handle_validation_error(request: Request, exc: ValidationError) -> JSONResponse:
        """Convert ValidationError to 422 response."""
        logger.opt(exception=exc).error(
            f"422 ValidationError on {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc) if str(exc) else "Validation failed"},
        )

    @app.exception_handler(RecordLimitReachedError)
    async def handle_record_limit_reached(_: Request, exc: RecordLimitReachedError) -> JSONResponse:
        """Convert RecordLimitReachedError to 409 with machine-readable code."""
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": str(exc) if str(exc) else "Record limit reached",
                "code": RecordLimitReachedError.error_code,
            },
        )

    @app.exception_handler(RecordUniquePerUserError)
    async def handle_record_unique_per_user(
        _: Request, exc: RecordUniquePerUserError
    ) -> JSONResponse:
        """Convert RecordUniquePerUserError to 409 with machine-readable code."""
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": str(exc) if str(exc) else "User already has a record of this type",
                "code": RecordUniquePerUserError.error_code,
            },
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
    async def handle_database_error(_: Request, exc: DatabaseError) -> JSONResponse:
        """Convert DatabaseError to 500 response."""
        logger.opt(exception=exc).error("Database error")
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
    async def handle_slicer_error(request: Request, exc: SlicerError) -> JSONResponse:
        """Convert SlicerError to 422 response."""
        logger.opt(exception=exc).error(
            f"422 SlicerError on {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc) if str(exc) else "Slicer operation failed"},
        )

    @app.exception_handler(PipelineError)
    async def handle_pipeline_error(_: Request, exc: PipelineError) -> JSONResponse:
        """Convert PipelineError to 500 response."""
        logger.opt(exception=exc).error("Pipeline error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc) if str(exc) else "Pipeline operation failed"},
        )

    @app.exception_handler(ReportQueryError)
    async def handle_report_query_error(_: Request, exc: ReportQueryError) -> JSONResponse:
        """Convert ReportQueryError to 500 response (SQL failure or timeout)."""
        logger.opt(exception=exc).error("Report query failed")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc) if str(exc) else "Report query failed"},
        )

    @app.exception_handler(ConfigurationError)
    async def handle_configuration_error(_: Request, exc: ConfigurationError) -> JSONResponse:
        """Convert ConfigurationError to 500 response."""
        logger.opt(exception=exc).error("Configuration error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Server configuration error"},
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
        logger.opt(exception=exc).error("Anonymization failed")
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

    from sqlalchemy.exc import ArgumentError, IntegrityError, InvalidRequestError, StatementError

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        """Convert SQLAlchemy IntegrityError to 409 response."""
        logger.warning("Database integrity error: {}", exc.orig)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": "Resource conflict"},
        )

    @app.exception_handler(ArgumentError)
    async def handle_argument_error(request: Request, exc: ArgumentError) -> JSONResponse:
        """Convert SQLAlchemy ArgumentError (ambiguous joins, etc.) to 422."""
        logger.opt(exception=exc).error(
            f"422 ArgumentError on {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid query parameters"},
        )

    @app.exception_handler(InvalidRequestError)
    async def handle_invalid_request_error(
        request: Request, exc: InvalidRequestError
    ) -> JSONResponse:
        """Convert SQLAlchemy InvalidRequestError to 422 response."""
        logger.opt(exception=exc).error(
            f"422 InvalidRequestError on {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid query parameters"},
        )

    @app.exception_handler(StatementError)
    async def handle_statement_error(request: Request, exc: StatementError) -> JSONResponse:
        """Convert SQLAlchemy StatementError (null bytes, type mismatches) to 422."""
        logger.opt(exception=exc).error(
            f"422 StatementError on {request.method} {request.url.path}: {exc.orig}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Invalid data for database operation"},
        )

    @app.exception_handler(OverflowError)
    async def handle_overflow_error(request: Request, exc: OverflowError) -> JSONResponse:
        """Convert OverflowError to 422 response."""
        logger.opt(exception=exc).error(
            f"422 OverflowError on {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "Numeric value out of range"},
        )

    @app.exception_handler(ValueError)
    async def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        """Convert ValueError (embedded null byte, etc.) to 422."""
        logger.opt(exception=exc).error(
            f"422 ValueError on {request.method} {request.url.path}: {exc}"
        )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def handle_unhandled_exception(_: Request, exc: Exception) -> JSONResponse:
        """Catch-all for unhandled exceptions — log full traceback, return generic 500."""
        logger.opt(exception=exc).error("Unhandled exception")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )
