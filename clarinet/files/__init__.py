"""Public facade for on-disk path resolution and file access.

Only ``Files`` (and ``AnonPathError`` for ``except`` clauses), plus
``PLACEHOLDER_REGEX`` for matching ``{placeholder}`` tokens and the
path-safety primitives ``validate_file_pattern``, ``assert_path_safe_value``,
and ``join_within``, are public. Lazy ``__getattr__`` keeps this package
import-light so the stdlib-only ``clarinet.files._template`` leaf stays
importable from ``clarinet.settings`` without dragging in models / services
(avoids a bootstrap import cycle) — and so no caller ever needs to import
that private leaf directly.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarinet.exceptions.domain import AnonPathError
    from clarinet.files._patterns import PLACEHOLDER_REGEX
    from clarinet.files._template import (
        assert_path_safe_value,
        join_within,
        validate_file_pattern,
    )
    from clarinet.files.facade import Files

__all__ = [
    "PLACEHOLDER_REGEX",
    "AnonPathError",
    "Files",
    "assert_path_safe_value",
    "join_within",
    "validate_file_pattern",
]


def __getattr__(name: str) -> object:
    if name == "Files":
        from clarinet.files.facade import Files

        return Files
    if name == "AnonPathError":
        from clarinet.exceptions.domain import AnonPathError

        return AnonPathError
    if name == "PLACEHOLDER_REGEX":
        from clarinet.files._patterns import PLACEHOLDER_REGEX

        return PLACEHOLDER_REGEX
    if name == "validate_file_pattern":
        from clarinet.files._template import validate_file_pattern

        return validate_file_pattern
    if name == "assert_path_safe_value":
        from clarinet.files._template import assert_path_safe_value

        return assert_path_safe_value
    if name == "join_within":
        from clarinet.files._template import join_within

        return join_within
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
