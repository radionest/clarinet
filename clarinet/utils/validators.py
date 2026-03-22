"""Shared validation utilities for the Clarinet framework.

Provides reusable validators used by both config-level primitives
and database-level models to enforce consistent naming conventions.
"""

import re

SLUG_RE = re.compile(r"^[a-z][-a-z0-9]{0,29}$")

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
