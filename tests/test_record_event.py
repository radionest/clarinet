"""Unit tests for RecordService audit event writes and the audit actor dependency."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from clarinet.models import RecordStatus
from clarinet.models.record_event import RecordEvent
from clarinet.services.record_service import RecordService


def _service(repo_mock: AsyncMock) -> tuple[RecordService, AsyncMock]:
    event_repo = AsyncMock()
    return RecordService(repo_mock, engine=None, event_repo=event_repo), event_repo


def _added_event(event_repo: AsyncMock) -> RecordEvent:
    event_repo.add.assert_awaited_once()
    event: RecordEvent = event_repo.add.call_args.args[0]
    return event


class TestRecordServiceAuditEvents:
    @pytest.mark.asyncio
    async def test_fail_record_writes_failed_with_reason(self) -> None:
        actor = uuid4()
        record_mock = MagicMock()
        record_mock.status = RecordStatus.failed

        repo_mock = AsyncMock()
        repo_mock.fail_record.return_value = (record_mock, RecordStatus.inwork)
        service, event_repo = _service(repo_mock)

        await service.fail_record(1, "broken series", actor_id=actor)

        event = _added_event(event_repo)
        assert event.kind == "failed"
        assert event.reason == "broken series"
        assert event.from_status == "inwork"
        assert event.to_status == "failed"

    @pytest.mark.asyncio
    async def test_soft_invalidate_writes_event_without_transition(self) -> None:
        old_record = MagicMock()
        old_record.status = RecordStatus.finished
        record_mock = MagicMock()
        record_mock.status = RecordStatus.finished

        repo_mock = AsyncMock()
        repo_mock.get.return_value = old_record
        repo_mock.invalidate_record.return_value = record_mock
        service, event_repo = _service(repo_mock)

        await service.invalidate_record(1, "soft", source_record_id=7, reason="stale input")

        event = _added_event(event_repo)
        assert event.kind == "invalidated"
        assert event.from_status is None
        assert event.to_status is None
        assert event.new_value == {"mode": "soft", "source_record_id": 7}
        assert event.reason == "stale input"

    @pytest.mark.asyncio
    async def test_create_record_writes_created(self) -> None:
        actor = uuid4()
        record_mock = MagicMock()
        record_mock.id = 5
        record_mock.status = RecordStatus.pending
        record_mock.record_type_name = "test-rt"
        record_mock.parent_record_id = None

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        service, event_repo = _service(repo_mock)

        with (
            patch("clarinet.services.record_service.RecordRead"),
            patch(
                "clarinet.services.record_service.validate_record_files",
                new=AsyncMock(return_value=None),
            ),
        ):
            await service.create_record(record_mock, actor_id=actor)

        event = _added_event(event_repo)
        assert event.kind == "created"
        assert event.record_id == 5
        assert event.to_status == "pending"
        assert event.new_value == {"record_type_name": "test-rt"}

    @pytest.mark.asyncio
    async def test_clear_output_files_writes_files_cleared(self) -> None:
        actor = uuid4()
        record_mock = MagicMock()
        record_mock.status = RecordStatus.failed
        record_mock.parent_record_id = None

        repo_mock = AsyncMock()
        repo_mock.get_with_relations.return_value = record_mock
        repo_mock.delete_output_file_links.return_value = 2
        service, event_repo = _service(repo_mock)

        with (
            patch("clarinet.services.record_service.RecordRead"),
            patch.object(service, "_collect_output_file_paths", new=AsyncMock(return_value=[])),
        ):
            await service.clear_output_files(1, actor_id=actor)

        event = _added_event(event_repo)
        assert event.kind == "files_cleared"
        assert event.new_value == {"files": [], "links": 2}

    @pytest.mark.asyncio
    async def test_update_context_info_keeps_old_and_new(self) -> None:
        actor = uuid4()
        record_mock = MagicMock()
        record_mock.context_info = "old text"

        repo_mock = AsyncMock()
        repo_mock.get.return_value = record_mock
        repo_mock.update_fields.return_value = record_mock
        service, event_repo = _service(repo_mock)

        await service.update_context_info(1, "new text", actor_id=actor)

        repo_mock.update_fields.assert_awaited_once_with(1, {"context_info": "new text"})
        event = _added_event(event_repo)
        assert event.kind == "context_info_updated"
        assert event.old_value == {"context_info": "old text"}
        assert event.new_value == {"context_info": "new text"}


class TestGetAuditActor:
    def _request(self, headers: dict[str, str]) -> SimpleNamespace:
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host="10.0.0.1"))

    @pytest.mark.asyncio
    async def test_browser_user_is_actor(self) -> None:
        from clarinet.api.dependencies import get_audit_actor

        user = MagicMock()
        user.id = uuid4()
        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            assert await get_audit_actor(self._request({}), user) == user.id

    @pytest.mark.asyncio
    async def test_service_token_maps_to_none(self) -> None:
        from clarinet.api.dependencies import get_audit_actor

        user = MagicMock()
        user.id = uuid4()
        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            settings_mock.login_lockout_minutes = 0
            request = self._request({"X-Internal-Token": "secret-token"})
            assert await get_audit_actor(request, user) is None

    @pytest.mark.asyncio
    async def test_wrong_token_falls_back_to_user(self) -> None:
        from clarinet.api.dependencies import get_audit_actor

        user = MagicMock()
        user.id = uuid4()
        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            settings_mock.login_lockout_minutes = 0
            request = self._request({"X-Internal-Token": "wrong"})
            assert await get_audit_actor(request, user) == user.id

    def test_empty_effective_token_never_matches(self) -> None:
        from clarinet.api.auth_config import is_service_request

        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = ""
            assert is_service_request(self._request({"X-Internal-Token": ""})) is False
            assert is_service_request(self._request({"X-Internal-Token": "x"})) is False
