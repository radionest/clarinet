"""Shared validation utilities for the Clarinet framework.

Provides reusable validators used by both config-level primitives
and database-level models to enforce consistent naming conventions.
"""

import re
from collections.abc import Iterable
from typing import Any

SLUG_RE = re.compile(r"^[a-z][-a-z0-9]{0,29}$")

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

_SLUG_ERROR_MSG = (
    "RecordType name must be a lowercase slug (letters, digits, hyphens; no underscores): got '{}'"
)


def validate_slug(value: str) -> str:
    """Validate that *value* is a lowercase kebab-case slug.

    Args:
        value: The string to validate.

    Returns:
        The validated string (unchanged).

    Raises:
        ValueError: If the string doesn't match the slug pattern.
    """
    if not SLUG_RE.match(value):
        raise ValueError(_SLUG_ERROR_MSG.format(value))
    return value


def validate_json_safe(value: Any) -> Any:
    """Reject integers outside i64 range in nested JSON structures.

    orjson (used by ORJSONResponse) only supports 64-bit integers.
    """
    nested: Iterable[Any] = ()
    match value:
        case int() if not isinstance(value, bool):
            if value < INT64_MIN or value > INT64_MAX:
                raise ValueError(f"Integer {value} exceeds 64-bit range")
        case dict():
            nested = value.values()
        case list():
            nested = value
    for item in nested:
        validate_json_safe(item)
    return value
