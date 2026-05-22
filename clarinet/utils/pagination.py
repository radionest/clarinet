"""Cursor-based keyset pagination utilities."""

import base64
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from clarinet.exceptions.domain import ValidationError

SortOrder = Literal[
    "changed_at_desc",
    "id_asc",
    "id_desc",
    "record_type_asc",
    "record_type_desc",
    "status_asc",
    "status_desc",
    "patient_asc",
    "patient_desc",
    "user_asc",
    "user_desc",
    "modality_asc",
    "modality_desc",
]


class InvalidCursorError(ValidationError):
    """Raised when a pagination cursor is malformed or mismatches sort order."""

    def __init__(self, detail: str = "Invalid pagination cursor"):
        super().__init__(detail)


def _to_json_safe(value: Any) -> Any:
    """Convert sort-key values to JSON-serializable primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def encode_cursor(sort: SortOrder, sort_key: Any, record_id: int) -> str:
    """Encode a keyset cursor.

    `sort_key` is the value of the sort column for the last row on the page.
    For `id_asc` / `id_desc` it is `None` (id alone determines order). For
    other sort orders it is the corresponding column value: datetime for
    changed_at, str for record_type/patient/modality, RecordStatus for status,
    UUID (possibly None) for user_id.
    """
    payload = {
        "v": 1,
        "o": sort,
        "k": _to_json_safe(sort_key),
        "i": record_id,
    }
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str, expected_sort: SortOrder) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
    except Exception as exc:
        raise InvalidCursorError(f"Cannot decode cursor: {exc}") from exc

    if data.get("v") != 1:
        raise InvalidCursorError(f"Unsupported cursor version: {data.get('v')}")
    if data.get("o") != expected_sort:
        raise InvalidCursorError(
            f"Cursor sort '{data.get('o')}' does not match requested sort '{expected_sort}'"
        )

    return dict(data)
