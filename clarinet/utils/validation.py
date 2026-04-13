"""
JSON validation utilities for the Clarinet framework.

This module provides utilities for validating JSON data against JSON schemas.
"""

import copy
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


def validate_json_by_schema_partial(json_data: Any, json_schema: dict[str, Any]) -> bool:
    """Validate JSON data against a schema with ``required`` constraints removed.

    Validates types, formats, and constraints but allows missing fields.
    Useful for prefill data that is intentionally incomplete.

    Args:
        json_data: The data to validate.
        json_schema: The schema to validate against (not mutated).

    Returns:
        True if validation succeeds.

    Raises:
        ValidationError: If validation fails or schema is invalid.
    """
    stripped = copy.deepcopy(json_schema)
    _strip_required(stripped)
    return validate_json_by_schema(json_data, stripped)


def _strip_required(schema: dict[str, Any]) -> None:
    """Recursively remove ``required`` from a JSON Schema in-place."""
    schema.pop("required", None)
    for prop in schema.get("properties", {}).values():
        if isinstance(prop, dict):
            _strip_required(prop)
    # Handle both Draft 2019-09+ ($defs) and Draft 4/7 (definitions)
    for key in ("$defs", "definitions"):
        for sub in schema.get(key, {}).values():
            if isinstance(sub, dict):
                _strip_required(sub)
    # Handle items in array schemas
    items = schema.get("items")
    if isinstance(items, dict):
        _strip_required(items)
    # Handle composition keywords (hydrate_schema generates oneOf)
    for key in ("allOf", "anyOf", "oneOf"):
        for sub in schema.get(key, []):
            if isinstance(sub, dict):
                _strip_required(sub)
    for key in ("if", "then", "else"):
        branch = schema.get(key)
        if isinstance(branch, dict):
            _strip_required(branch)
