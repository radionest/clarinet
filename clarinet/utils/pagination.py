"""Cursor-based keyset pagination utilities."""

import base64
import json
from datetime import datetime
from typing import Any, Literal

from clarinet.exceptions.domain import ValidationError

SortOrder = Literal["changed_at_desc", "id_asc", "id_desc"]


class InvalidCursorError(ValidationError):
    """Raised when a pagination cursor is malformed or mismatches sort order."""

    def __init__(self, detail: str = "Invalid pagination cursor"):
        super().__init__(detail)


def encode_cursor(sort: SortOrder, changed_at: datetime | None, record_id: int) -> str:
    payload = {
        "v": 1,
        "o": sort,
        "k": changed_at.isoformat() if changed_at else None,
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
