"""Utility functions for file pattern processing.

This module provides functions for resolving file patterns with placeholders
and finding files in directories.

Virtual fields
--------------
``_VIRTUAL_FIELD_MAP`` defines aliases that map to real record fields but use
**inverted** resolution priority.  Regular placeholders resolve primary record
first, then fallbacks.  Virtual fields resolve fallbacks first (e.g. parent),
then the primary record.  This is intentional: ``{origin_type}`` should
resolve to the *parent's* record type when a file was produced by a different
record type and the current record is consuming it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.record import RecordBase, RecordRead

PLACEHOLDER_REGEX = re.compile(r"\{([^}]+)\}")

# Virtual fields: short alias → real dotted path on the record.
# Resolution priority is *inverted* (fallbacks first, then primary)
# so that e.g. ``{origin_type}`` resolves to the *parent's* type when
# a parent is available, falling back to the record's own type otherwise.
_VIRTUAL_FIELD_MAP: dict[str, str] = {
    "origin_type": "record_type.name",
}


def resolve_origin_type(record: RecordRead, parent: RecordRead | None = None) -> str:
    """Resolve the ``origin_type`` virtual field for a record.

    Returns the parent's record type name when a parent is available,
    falling back to the record's own type name otherwise.

    Args:
        record: The current record.
        parent: Optional parent record.

    Returns:
        Record type name to use as ``origin_type``.
    """
    if parent is not None:
        return parent.record_type.name
    return record.record_type.name


def resolve_record_field(record: RecordBase, field_path: str) -> str:
    """Get value of a field from record by path.

    Supports paths:
        - Simple fields: id, user_id, patient_id, study_uid, series_uid
        - Nested data fields: data.FIELD (only first level)
        - Nested record_type fields: record_type.FIELD (only first level)

    Args:
        record: Record instance to get field value from
        field_path: Dot-separated path to the field

    Returns:
        String representation of the field value, or empty string if not found

    Examples:
        >>> resolve_record_field(record, "id")
        "42"
        >>> resolve_record_field(record, "data.BIRADS_R")
        "4"
        >>> resolve_record_field(record, "record_type.name")
        "ct_segmentation"
    """
    parts = field_path.split(".")

    # Get the root object
    obj: Any = record
    for part in parts:
        if obj is None:
            return ""

        if isinstance(obj, dict):
            obj = obj.get(part)
        elif hasattr(obj, part):
            obj = getattr(obj, part)
        else:
            return ""

    return str(obj) if obj is not None else ""


def resolve_pattern(
    pattern: str,
    record: RecordBase,
    *fallbacks: RecordBase | None,
) -> str:
    """Replace placeholders {field} with values from record, with fallback chain.

    Tries the primary record first, then each fallback in order. This allows
    patterns like ``{user_id}`` to resolve from a parent record when the
    current record (e.g. an auto-record) has no user.

    Args:
        pattern: Pattern string with placeholders like {id}, {data.FIELD}
        record: Primary record instance to get values from
        *fallbacks: Additional records to try if the field is empty on primary

    Returns:
        Pattern with placeholders replaced by actual values

    Examples:
        >>> resolve_pattern("result_{id}.json", record)
        "result_42.json"
        >>> resolve_pattern("seg_{user_id}.nrrd", auto_record, parent_record)
        "seg_user-123.nrrd"  # user_id from parent
    """

    def replacer(match: re.Match[str]) -> str:
        field_path = match.group(1)

        if field_path in _VIRTUAL_FIELD_MAP:
            # Virtual fields use *inverted* priority: try fallbacks first,
            # then the primary record.  This lets ``{origin_type}`` resolve
            # to the parent record's type when consuming a file produced by
            # a different record type.
            real_path = _VIRTUAL_FIELD_MAP[field_path]
            value = ""
            for fb in fallbacks:
                if fb is not None:
                    value = resolve_record_field(fb, real_path)
                    if value:
                        break
            if not value:
                value = resolve_record_field(record, real_path)
            return value

        value = resolve_record_field(record, field_path)
        if not value:
            for fb in fallbacks:
                if fb is not None:
                    value = resolve_record_field(fb, field_path)
                    if value:
                        break
        return value

    return PLACEHOLDER_REGEX.sub(replacer, pattern)


def glob_file_paths(
    fd: FileDefinitionRead,
    working_dir: Path,
) -> list[Path]:
    """Glob collection file pattern, replacing placeholders with wildcards.

    Args:
        fd: File definition with pattern (should have multiple=True)
        working_dir: Base directory to glob in

    Returns:
        Sorted list of matching Paths
    """
    glob_pattern = PLACEHOLDER_REGEX.sub("*", fd.pattern)
    return sorted(working_dir.glob(glob_pattern))
