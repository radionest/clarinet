"""Unit tests for cursor-based pagination utilities."""

from datetime import UTC, datetime

import pytest

from clarinet.utils.pagination import (
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
)


class TestCursorRoundTrip:
    def test_changed_at_desc_round_trip(self) -> None:
        ts = datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)
        cursor = encode_cursor("changed_at_desc", ts, 42)
        data = decode_cursor(cursor, "changed_at_desc")
        assert data["v"] == 1
        assert data["o"] == "changed_at_desc"
        assert data["k"] == ts.isoformat()
        assert data["i"] == 42

    def test_id_asc_round_trip(self) -> None:
        cursor = encode_cursor("id_asc", None, 100)
        data = decode_cursor(cursor, "id_asc")
        assert data["o"] == "id_asc"
        assert data["k"] is None
        assert data["i"] == 100

    def test_id_desc_round_trip(self) -> None:
        cursor = encode_cursor("id_desc", None, 999)
        data = decode_cursor(cursor, "id_desc")
        assert data["o"] == "id_desc"
        assert data["i"] == 999

    def test_none_changed_at(self) -> None:
        cursor = encode_cursor("changed_at_desc", None, 1)
        data = decode_cursor(cursor, "changed_at_desc")
        assert data["k"] is None


class TestDecodeCursorErrors:
    def test_invalid_base64(self) -> None:
        with pytest.raises(InvalidCursorError, match="Cannot decode cursor"):
            decode_cursor("!!!invalid!!!", "changed_at_desc")

    def test_invalid_json(self) -> None:
        import base64

        bad = base64.urlsafe_b64encode(b"not json").decode().rstrip("=")
        with pytest.raises(InvalidCursorError, match="Cannot decode cursor"):
            decode_cursor(bad, "changed_at_desc")

    def test_wrong_version(self) -> None:
        import base64
        import json

        payload = json.dumps({"v": 99, "o": "changed_at_desc", "k": None, "i": 1})
        cursor = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        with pytest.raises(InvalidCursorError, match="Unsupported cursor version"):
            decode_cursor(cursor, "changed_at_desc")

    def test_wrong_sort(self) -> None:
        cursor = encode_cursor("changed_at_desc", None, 1)
        with pytest.raises(InvalidCursorError, match="does not match"):
            decode_cursor(cursor, "id_asc")

    def test_empty_string(self) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor("", "changed_at_desc")


class TestInvalidCursorErrorIsValidation:
    def test_is_validation_error(self) -> None:
        from clarinet.exceptions.domain import ValidationError

        assert issubclass(InvalidCursorError, ValidationError)
