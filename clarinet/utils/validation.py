"""
JSON validation utilities for the Clarinet framework.

This module provides utilities for validating JSON data against JSON schemas.
"""

import copy
import itertools
from collections.abc import Iterable
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from ..exceptions.domain import FieldError, RecordDataValidationError, ValidationError
from ..utils.logger import logger

# Cap the number of JSON-Schema errors aggregated into a single 422 payload.
# ``iter_errors()`` can yield dozens of issues for a heavily malformed object;
# bounding the list keeps the response body and the UI list readable.
_MAX_SCHEMA_ERRORS = 10


def _json_pointer(path: Iterable[Any]) -> str:
    """Build a JSON Pointer (RFC 6901) from a jsonschema ``absolute_path`` deque.

    Each segment is escaped per RFC 6901 §4: ``~`` becomes ``~0`` and ``/``
    becomes ``~1`` (in that order, so ``~/`` round-trips correctly).
    Returns ``""`` for an error at the document root (empty path).
    """
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in path]
    if not parts:
        return ""
    return "/" + "/".join(parts)


def _required_property_path(error: Any) -> list[Any] | None:
    """Extract the missing property name from a jsonschema ``required`` error.

    jsonschema reports ``required`` failures with ``absolute_path`` pointing
    at the *parent* object (often empty for the document root), leaving the
    missing field name only in the message ``"'foo' is a required property"``.
    Returning ``[..., "foo"]`` lets the frontend highlight the actual field
    instead of pointing the user at the document root.

    Returns ``None`` if the message does not match the expected shape, in
    which case the caller should fall back to ``absolute_path``.
    """
    msg = getattr(error, "message", "")
    if not msg.endswith("is a required property"):
        return None
    # Format: ``'<name>' is a required property`` (single-quoted).
    if len(msg) < 2 or msg[0] != "'":
        return None
    closing = msg.find("'", 1)
    if closing <= 1:
        return None
    return [*error.absolute_path, msg[1:closing]]


def validate_json_by_schema(json_data: Any, json_schema: dict[str, Any]) -> None:
    """Validate JSON data against a JSON schema.

    Aggregates up to :data:`_MAX_SCHEMA_ERRORS` issues per call (collected via
    ``Draft202012Validator.iter_errors``), so the user sees several form
    problems at once rather than fixing them one-by-one.

    Args:
        json_data: The data to validate.
        json_schema: The schema to validate against.

    Raises:
        RecordDataValidationError: If validation fails — carries one
            :class:`FieldError` per jsonschema error
            (``path`` = JSON Pointer, ``code`` = jsonschema validator name
            e.g. ``"minimum"`` / ``"required"`` / ``"type"``).
        ValidationError: If the schema itself is invalid.
    """
    # ``Draft202012Validator(json_schema)`` is lazy — it doesn't raise
    # ``SchemaError`` for structural problems (e.g. unknown ``type``) until
    # the first call to ``iter_errors()``, and then it surfaces as
    # ``jsonschema.exceptions.UnknownType``/etc. instead of ``SchemaError``.
    # Run the explicit ``check_schema()`` upfront so we always get a clean
    # ``SchemaError`` → ``ValidationError(Invalid JSON schema: ...)`` for
    # malformed schemas, regardless of whether the data has issues.
    try:
        Draft202012Validator.check_schema(json_schema)
    except SchemaError as e:
        logger.error(f"JSON schema error: {e}")
        raise ValidationError(f"Invalid JSON schema: {e!s}") from e

    validator = Draft202012Validator(json_schema)
    errors = list(itertools.islice(validator.iter_errors(json_data), _MAX_SCHEMA_ERRORS))
    if not errors:
        return

    field_errors = []
    for err in errors:
        # For ``required`` errors, jsonschema's absolute_path stops at the
        # parent object — splice the missing property name onto the path so
        # the frontend can highlight the actual field rather than the root.
        if err.validator == "required":
            required_path = _required_property_path(err)
            path_parts = required_path if required_path is not None else err.absolute_path
        else:
            path_parts = err.absolute_path
        field_errors.append(
            FieldError(
                path=_json_pointer(path_parts),
                message=err.message,
                code=err.validator or "schema",
            )
        )
    logger.error(
        f"JSON validation: {len(field_errors)} error(s) — first at "
        f"'{field_errors[0].path}': {field_errors[0].message}"
    )
    raise RecordDataValidationError(field_errors)


def validate_json_by_schema_partial(json_data: Any, json_schema: dict[str, Any]) -> None:
    """Validate JSON data against a schema with ``required`` constraints removed.

    Validates types, formats, and constraints but allows missing fields.
    Useful for prefill data that is intentionally incomplete.

    Args:
        json_data: The data to validate.
        json_schema: The schema to validate against (not mutated).

    Raises:
        RecordDataValidationError: If validation fails.
        ValidationError: If the schema itself is invalid.
    """
    stripped = copy.deepcopy(json_schema)
    _strip_required(stripped)
    validate_json_by_schema(json_data, stripped)


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
