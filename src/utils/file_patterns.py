"""
Utility functions for file pattern processing.

This module provides functions for resolving file patterns with placeholders,
matching filenames against patterns, and finding files in directories.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.models.record import Record

PLACEHOLDER_REGEX = re.compile(r"\{([^}]+)\}")


def resolve_record_field(record: Record, field_path: str) -> str:
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


def resolve_pattern(pattern: str, record: Record) -> str:
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


def match_filename(filename: str, pattern: str, record: Record) -> bool:
    """Check if filename matches the pattern (exact match).

    Args:
        filename: Filename to check
        pattern: Pattern with placeholders
        record: Record instance for placeholder resolution

    Returns:
        True if filename exactly matches the resolved pattern

    Examples:
        >>> match_filename("result_42.json", "result_{id}.json", record)
        True
        >>> match_filename("result_99.json", "result_{id}.json", record)  # record.id == 42
        False
    """
    expected = resolve_pattern(pattern, record)
    return filename == expected


def find_matching_file(
    directory: Path,
    pattern: str,
    record: Record,
) -> str | None:
    """Find file in directory that matches the pattern.

    Args:
        directory: Directory to search in
        pattern: Pattern with placeholders
        record: Record instance for placeholder resolution

    Returns:
        Filename if found, None otherwise

    Examples:
        >>> find_matching_file(Path("/data"), "result_{id}.json", record)
        "result_42.json"  # if file exists
    """
    if not directory.exists():
        return None

    expected_name = resolve_pattern(pattern, record)
    expected_path = directory / expected_name

    if expected_path.is_file():
        return expected_name

    return None


def generate_filename(pattern: str, record: Record) -> str:
    """Generate filename from pattern using record values.

    This is an alias for resolve_pattern for semantic clarity.

    Args:
        pattern: Pattern with placeholders
        record: Record instance for placeholder resolution

    Returns:
        Generated filename

    Examples:
        >>> generate_filename("seg_{id}.seg.nrrd", record)
        "seg_42.seg.nrrd"
    """
    return resolve_pattern(pattern, record)
