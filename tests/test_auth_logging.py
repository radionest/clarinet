"""Regression tests for auth logging structure.

These tests guard the structured `extra={...}` payloads attached to login,
session-creation, and token-validation log records. The auth pipeline relies on
those payloads for jq filtering and forensic analysis (see
`.claude/rules/logging-pii.md`); a refactor that drops `extra=` would silently
break operational tooling without breaking unit tests of behavior — this file
exists to catch that.

Loguru quirk: `logger.info("msg", extra={"k": "v"})` nests the dict under
`record["extra"]["extra"]`, NOT `record["extra"]["k"]`. All assertions reflect
the project convention.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import Request

from clarinet.api.auth_config import DatabaseStrategy, UserManager
from clarinet.models.user import User
from clarinet.utils.logger import logger
from tests.utils.urls import AUTH_LOGIN


@pytest.fixture
def captured_records():
    """Capture every loguru record emitted during the test as raw dicts."""
    records: list[dict] = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    yield records
    logger.remove(sink_id)


def _records_for(records: list[dict], message_substring: str) -> list[dict]:
    """Filter captured records by a substring of the rendered message."""
    return [r for r in records if message_substring in r["message"]]


def _make_user(*, active: bool = True) -> User:
    user = MagicMock(spec=User)
    user.id = uuid4()
    user.email = "test@example.com"
    user.is_active = active
    return user


def _make_request(
    *,
    user_agent: str = "TestAgent/1.0",
    referer: str = "",
    path: str = AUTH_LOGIN,
    client_host: str = "10.0.0.1",
) -> MagicMock:
    """Build a fastapi.Request-shaped mock with the headers we care about.

    Uses ``spec=Request`` plus ``SimpleNamespace`` for ``url`` and ``client``
    so attribute typos fail loudly instead of being absorbed by MagicMock.
    """
    headers: dict[str, str] = {"User-Agent": user_agent}
    if referer:
        headers["Referer"] = referer
    request = MagicMock(spec=Request)
    request.headers = headers
    request.url = SimpleNamespace(path=path)
    request.client = SimpleNamespace(host=client_host)
    return request


def _strategy_with_no_token(*, request: MagicMock | None = None) -> DatabaseStrategy:
    """Build a DatabaseStrategy whose token query returns no row."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    return DatabaseStrategy(session=session, request=request)


class TestReadTokenFailureLogStructure:
    """Each token-validation failure branch must carry a structured `reason`.

    Operators rely on `jq 'select(.extra.extra.reason == "...")'` to slice
    /tmp/clarinet.log by failure mode; a missing `reason` is invisible until
    someone tries to debug a real incident.
    """

    @pytest.mark.asyncio
    async def test_not_found_or_expired_branch_logs_reason(self, captured_records):
        request = _make_request(path="/api/records/42")
        strategy = _strategy_with_no_token(request=request)

        with patch("clarinet.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 0
            result = await strategy.read_token("deadbeef" * 4, AsyncMock())

        assert result is None

        warnings = _records_for(captured_records, "Token validation failed")
        assert len(warnings) == 1, "expected exactly one validation-failure warning"
        record = warnings[0]
        assert record["level"].name == "WARNING"

        extra = record["extra"]["extra"]
        assert extra["reason"] == "not_found_or_expired"
        assert extra["request_path"] == "/api/records/42"
        assert extra["request_ip"] == "10.0.0.1"
        # Token is logged only as a short preview, never the full value
        assert extra["token_preview"] == "deadbeef"
        assert "deadbeefdeadbeefdeadbeefdeadbeef" not in record["message"]

    @pytest.mark.asyncio
    async def test_not_found_branch_handles_missing_request(self, captured_records):
        """When self.request is None, locals must default to None, not crash."""
        strategy = _strategy_with_no_token(request=None)

        with patch("clarinet.api.auth_config.settings") as mock_settings:
            mock_settings.session_cache_ttl_seconds = 0
            await strategy.read_token("token-xyz", AsyncMock())

        warnings = _records_for(captured_records, "Token validation failed")
        assert len(warnings) == 1
        extra = warnings[0]["extra"]["extra"]
        assert extra["reason"] == "not_found_or_expired"
        assert extra["request_path"] is None
        assert extra["request_ip"] is None


class TestWriteTokenLogStructure:
    """`write_token` must log user_agent so audit logs can correlate sessions."""

    @pytest.mark.asyncio
    async def test_session_created_log_includes_user_agent(self, captured_records):
        user = _make_user()
        request = _make_request(user_agent="Mozilla/5.0 (Slicer)")

        session = AsyncMock()
        session.add = MagicMock()
        strategy = DatabaseStrategy(session=session, request=request)

        with patch("clarinet.api.auth_config.settings") as mock_settings:
            mock_settings.session_concurrent_limit = 0
            mock_settings.session_expire_hours = 24
            await strategy.write_token(user)

        infos = _records_for(captured_records, "Session created for user")
        assert len(infos) == 1
        record = infos[0]
        assert record["level"].name == "INFO"

        extra = record["extra"]["extra"]
        assert extra["user_id"] == str(user.id)
        assert extra["user_agent"] == "Mozilla/5.0 (Slicer)"
        assert extra["ip_address"] == "10.0.0.1"
        assert "expires_at" in extra


class TestOnAfterLoginDebugStructure:
    """`UserManager.on_after_login` must emit DEBUG diagnostics for double-login forensics."""

    @pytest.fixture
    def manager(self) -> UserManager:
        # UserManager constructor only stores user_db; we never call DB methods here.
        return UserManager(user_db=MagicMock())

    @pytest.mark.asyncio
    async def test_info_log_carries_user_id_in_extra(self, manager, captured_records):
        user = _make_user()
        request = _make_request()

        await manager.on_after_login(user, request=request)

        infos = _records_for(captured_records, "logged in")
        assert len(infos) == 1
        assert infos[0]["level"].name == "INFO"
        # user_id is in `extra` so jq queries work without enabling DEBUG
        assert infos[0]["extra"]["extra"]["user_id"] == str(user.id)

    @pytest.mark.asyncio
    async def test_debug_log_includes_metadata(self, manager, captured_records):
        user = _make_user()
        request = _make_request(
            user_agent="OHIFViewer/3.0",
            referer="https://example.com/ohif/viewer",
            path=AUTH_LOGIN,
        )

        await manager.on_after_login(user, request=request)

        debugs = _records_for(captured_records, "Login request metadata")
        assert len(debugs) == 1
        record = debugs[0]
        assert record["level"].name == "DEBUG"

        extra = record["extra"]["extra"]
        assert extra["user_id"] == str(user.id)
        assert extra["user_agent"] == "OHIFViewer/3.0"
        # Path is dropped — only origin survives
        assert extra["referer"] == "https://example.com"
        assert extra["request_path"] == AUTH_LOGIN

    @pytest.mark.asyncio
    async def test_referer_path_query_and_fragment_are_stripped(self, manager, captured_records):
        """A Referer with secrets anywhere past the netloc must be sanitized."""
        user = _make_user()
        request = _make_request(
            referer="https://example.com/reset/SECRET_TOKEN?email=alice@example.com#frag",
        )

        await manager.on_after_login(user, request=request)

        debugs = _records_for(captured_records, "Login request metadata")
        assert len(debugs) == 1
        referer = debugs[0]["extra"]["extra"]["referer"]
        assert referer == "https://example.com"
        assert "SECRET_TOKEN" not in referer
        assert "reset" not in referer
        assert "alice@example.com" not in referer
        assert "frag" not in referer

    @pytest.mark.asyncio
    async def test_referer_path_only_input_dropped(self, manager, captured_records):
        """A path-only Referer (no scheme/host) collapses to empty string."""
        user = _make_user()
        request = _make_request(referer="/some/local/path?q=1")

        await manager.on_after_login(user, request=request)

        debugs = _records_for(captured_records, "Login request metadata")
        assert len(debugs) == 1
        # No scheme/netloc → safe origin is empty; path is never preserved
        assert debugs[0]["extra"]["extra"]["referer"] == ""

    @pytest.mark.asyncio
    async def test_missing_referer_logs_empty_string(self, manager, captured_records):
        user = _make_user()
        request = _make_request(referer="")

        await manager.on_after_login(user, request=request)

        debugs = _records_for(captured_records, "Login request metadata")
        assert len(debugs) == 1
        assert debugs[0]["extra"]["extra"]["referer"] == ""

    @pytest.mark.asyncio
    async def test_user_agent_truncated_to_512(self, manager, captured_records):
        user = _make_user()
        request = _make_request(user_agent="A" * 1000)

        await manager.on_after_login(user, request=request)

        debugs = _records_for(captured_records, "Login request metadata")
        assert len(debugs) == 1
        assert len(debugs[0]["extra"]["extra"]["user_agent"]) == 512

    @pytest.mark.asyncio
    async def test_referer_truncated_to_512(self, manager, captured_records):
        """An overlong Referer origin must be capped at 512 chars."""
        user = _make_user()
        # Long netloc so that scheme://netloc exceeds 512 chars
        long_host = "b" * 1000 + ".example.com"
        request = _make_request(referer=f"https://{long_host}/path?secret=x")

        await manager.on_after_login(user, request=request)

        debugs = _records_for(captured_records, "Login request metadata")
        assert len(debugs) == 1
        assert len(debugs[0]["extra"]["extra"]["referer"]) == 512

    @pytest.mark.asyncio
    async def test_no_request_skips_debug_log(self, manager, captured_records):
        """Without a Request, info log still fires but debug is skipped."""
        user = _make_user()
        await manager.on_after_login(user, request=None)

        assert _records_for(captured_records, "logged in")
        assert not _records_for(captured_records, "Login request metadata")
