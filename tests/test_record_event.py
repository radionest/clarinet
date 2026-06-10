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
    async def test_update_status_writes_status_changed(self) -> None:
        actor = uuid4()
        record_mock = MagicMock()
        record_mock.status = RecordStatus.inwork

        repo_mock = AsyncMock()
        repo_mock.update_status.return_value = (record_mock, RecordStatus.pending)
        service, event_repo = _service(repo_mock)

        await service.update_status(1, RecordStatus.inwork, actor_id=actor)

        event = _added_event(event_repo)
        assert event.kind == "status_changed"
        assert event.record_id == 1
        assert event.actor_id == actor
        assert event.from_status == "pending"
        assert event.to_status == "inwork"

    @pytest.mark.asyncio
    async def test_update_status_unchanged_writes_nothing(self) -> None:
        record_mock = MagicMock()
        record_mock.status = RecordStatus.pending

        repo_mock = AsyncMock()
        repo_mock.update_status.return_value = (record_mock, RecordStatus.pending)
        service, event_repo = _service(repo_mock)

        await service.update_status(1, RecordStatus.pending)

        event_repo.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_status_without_event_repo_is_noop(self) -> None:
        record_mock = MagicMock()
        record_mock.status = RecordStatus.inwork
        repo_mock = AsyncMock()
        repo_mock.update_status.return_value = (record_mock, RecordStatus.pending)

        service = RecordService(repo_mock, engine=None)
        await service.update_status(1, RecordStatus.inwork)  # must not raise

    @pytest.mark.asyncio
    async def test_assign_user_writes_assigned(self) -> None:
        actor = uuid4()
        target_user = uuid4()
        record_mock = MagicMock()
        record_mock.status = RecordStatus.inwork

        prefetch = MagicMock()
        prefetch.record_type.unique_per_user = False

        repo_mock = AsyncMock()
        repo_mock.get_with_record_type.return_value = prefetch
        repo_mock.assign_user.return_value = (record_mock, RecordStatus.pending)
        service, event_repo = _service(repo_mock)

        await service.assign_user(1, target_user, actor_id=actor)

        event = _added_event(event_repo)
        assert event.kind == "assigned"
        assert event.actor_id == actor
        assert event.new_value == {"user_id": str(target_user)}
        assert event.from_status == "pending"
        assert event.to_status == "inwork"

    @pytest.mark.asyncio
    async def test_unassign_user_writes_unassigned(self) -> None:
        record_mock = MagicMock()
        record_mock.status = RecordStatus.pending

        repo_mock = AsyncMock()
        repo_mock.unassign_user.return_value = (record_mock, RecordStatus.inwork)
        service, event_repo = _service(repo_mock)

        await service.unassign_user(1, actor_id=None)

        event = _added_event(event_repo)
        assert event.kind == "unassigned"
        assert event.actor_id is None
        assert event.from_status == "inwork"
        assert event.to_status == "pending"

    @pytest.mark.asyncio
    async def test_claim_record_writes_assigned_with_claim_reason(self) -> None:
        user_id = uuid4()
        prefetch = MagicMock()
        prefetch.status = RecordStatus.pending
        prefetch.record_type.unique_per_user = False
        claimed = MagicMock()
        claimed.status = RecordStatus.inwork

        repo_mock = AsyncMock()
        repo_mock.get_with_record_type.return_value = prefetch
        repo_mock.claim_record.return_value = claimed
        service, event_repo = _service(repo_mock)

        await service.claim_record(1, user_id, actor_id=user_id)

        event = _added_event(event_repo)
        assert event.kind == "assigned"
        assert event.reason == "claim"
        assert event.new_value == {"user_id": str(user_id)}

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
    async def test_submit_data_writes_field_names_only(self) -> None:
        record_mock = MagicMock()
        record_mock.status = RecordStatus.finished

        repo_mock = AsyncMock()
        repo_mock.update_data.return_value = (record_mock, RecordStatus.inwork)
        service, event_repo = _service(repo_mock)

        with patch("clarinet.services.record_service.RecordRead"):
            await service.submit_data(
                1, {"score": 0.9, "label": "x"}, RecordStatus.finished, actor_id=None
            )

        event = _added_event(event_repo)
        assert event.kind == "data_submitted"
        assert event.new_value == {"fields": ["label", "score"]}
        assert event.old_value is None  # data values are never copied into audit

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
        return SimpleNamespace(headers=headers)

    def test_browser_user_is_actor(self) -> None:
        from clarinet.api.dependencies import get_audit_actor

        user = MagicMock()
        user.id = uuid4()
        assert get_audit_actor(self._request({}), user) == user.id

    def test_service_token_maps_to_none(self) -> None:
        from clarinet.api.dependencies import get_audit_actor

        user = MagicMock()
        user.id = uuid4()
        with patch("clarinet.api.dependencies.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            request = self._request({"X-Internal-Token": "secret-token"})
            assert get_audit_actor(request, user) is None

    def test_wrong_token_falls_back_to_user(self) -> None:
        from clarinet.api.dependencies import get_audit_actor

        user = MagicMock()
        user.id = uuid4()
        with patch("clarinet.api.dependencies.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            request = self._request({"X-Internal-Token": "wrong"})
            assert get_audit_actor(request, user) == user.id
