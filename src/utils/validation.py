"""
JSON validation utilities for the Clarinet framework.

This module provides utilities for validating JSON data against JSON schemas.
"""

from typing import Any

from fastapi import HTTPException, status
from jsonschema import SchemaError, ValidationError, validate

from ..utils.logger import logger


def validate_json_by_schema(json_data: Any, json_schema: dict[str, Any]) -> bool:
    """
    Validate JSON data against a JSON schema.

    Args:
        json_data: The data to validate
        json_schema: The schema to validate against

    Returns:
        True if validation succeeds

    Raises:
        HTTPException: If validation fails
    """
    try:
        validate(instance=json_data, schema=json_schema)
    except ValidationError as e:
        logger.error(f"JSON validation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"JSON validation failed: {e!s}",
        ) from e
    except SchemaError as e:
        logger.error(f"JSON schema error: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON schema: {e!s}",
        ) from e
    return True
