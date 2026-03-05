"""
Utility functions for file pattern processing.

This module provides functions for resolving file patterns with placeholders
and finding files in directories.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models.file_schema import FileDefinitionRead
    from src.models.record import RecordBase

PLACEHOLDER_REGEX = re.compile(r"\{([^}]+)\}")


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


def resolve_pattern(pattern: str, record: RecordBase) -> str:
    """Replace placeholders {field} with values from record.

    Args:
        pattern: Pattern string with placeholders like {id}, {data.FIELD}
        record: Record instance to get values from

    Returns:
        Pattern with placeholders replaced by actual values

    Examples:
        >>> resolve_pattern("result_{id}.json", record)
        "result_42.json"
        >>> resolve_pattern("birads_{data.BIRADS_R}.txt", record)
        "birads_4.txt"
        >>> resolve_pattern("seg_{study_uid}_{id}.seg.nrrd", record)
        "seg_1.2.3.4.5_42.seg.nrrd"
    """

    def replacer(match: re.Match[str]) -> str:
        field_path = match.group(1)
        return resolve_record_field(record, field_path)

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
