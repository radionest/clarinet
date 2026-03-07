"""
JSON validation utilities for the Clarinet framework.

This module provides utilities for validating JSON data against JSON schemas.
"""

from typing import Any

from jsonschema import SchemaError, validate
from jsonschema import ValidationError as JsonSchemaValidationError

from ..exceptions.domain import ValidationError
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
        ValidationError: If validation fails or schema is invalid
    """
    try:
        validate(instance=json_data, schema=json_schema)
    except JsonSchemaValidationError as e:
        logger.error(f"JSON validation error: {e}")
        raise ValidationError(f"JSON validation failed: {e!s}") from e
    except SchemaError as e:
        logger.error(f"JSON schema error: {e}")
        raise ValidationError(f"Invalid JSON schema: {e!s}") from e
    return True
