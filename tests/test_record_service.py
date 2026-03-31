"""Unit tests for RecordService and StudyService RecordFlow triggers."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from clarinet.models import RecordStatus
from clarinet.services.record_service import RecordService
from clarinet.services.study_service import StudyService


class TestRecordServiceTriggers:
    """Test RecordService mutation methods fire correct RecordFlow triggers."""

    @pytest.mark.asyncio
    async def test_update_status_fires_status_change_trigger(self) -> None:
        """Test update_status fires status-change trigger when status changes."""
        record_mock = MagicMock()
        record_mock.status = RecordStatus.inwork
        old_status = RecordStatus.pending
        record_read_mock = MagicMock()

        repo_mock = AsyncMock()
        repo_mock.update_status.return_value = (record_mock, old_status)

        engine_mock = AsyncMock()

        service = RecordService(repo_mock, engine_mock)

        with patch("clarinet.services.record_service.RecordRead") as patched:
            patched.model_validate.return_value = record_read_mock
            result, result_old_status = await service.update_status(1, RecordStatus.inwork)

            repo_mock.update_status.assert_awaited_once_with(1, RecordStatus.inwork)
            patched.model_validate.assert_called_once_with(record_mock)
            engine_mock.handle_record_status_change.assert_awaited_once_with(
                record_read_mock, old_status
            )
            assert result == record_mock
            assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_update_status_no_trigger_when_status_unchanged(self) -> None:
        """Test update_status does not fire trigger when status unchanged."""
        record_mock = MagicMock()
        record_mock.status = RecordStatus.pending
        old_status = RecordStatus.pending

        repo_mock = AsyncMock()
        repo_mock.update_status.return_value = (record_mock, old_status)

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)

        result, result_old_status = await service.update_status(1, RecordStatus.pending)

        repo_mock.update_status.assert_awaited_once_with(1, RecordStatus.pending)
        engine_mock.handle_record_status_change.assert_not_awaited()
        assert result == record_mock
        assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_update_status_no_trigger_when_engine_none(self) -> None:
        """Test update_status does not fire trigger when engine is None."""
        record_mock = MagicMock()
        record_mock.status = RecordStatus.inwork
        old_status = RecordStatus.pending

        repo_mock = AsyncMock()
        repo_mock.update_status.return_value = (record_mock, old_status)

        service = RecordService(repo_mock, engine=None)

        result, result_old_status = await service.update_status(1, RecordStatus.inwork)

        repo_mock.update_status.assert_awaited_once_with(1, RecordStatus.inwork)
        assert result == record_mock
        assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_assign_user_fires_status_change_trigger(self) -> None:
        """Test assign_user fires status-change trigger when status changes."""
        user_id = uuid4()
        record_mock = MagicMock()
        record_mock.status = RecordStatus.inwork
        old_status = RecordStatus.pending
        record_read_mock = MagicMock()

        prefetch_mock = MagicMock()
        prefetch_mock.record_type.unique_per_user = False

        repo_mock = AsyncMock()
        repo_mock.get_with_record_type.return_value = prefetch_mock
        repo_mock.assign_user.return_value = (record_mock, old_status)

        engine_mock = AsyncMock()

        service = RecordService(repo_mock, engine_mock)

        with patch("clarinet.services.record_service.RecordRead") as patched:
            patched.model_validate.return_value = record_read_mock
            result, result_old_status = await service.assign_user(1, user_id)

            repo_mock.get_with_record_type.assert_awaited_once_with(1)
            repo_mock.assign_user.assert_awaited_once_with(1, user_id)
            patched.model_validate.assert_called_once_with(record_mock)
            engine_mock.handle_record_status_change.assert_awaited_once_with(
                record_read_mock, old_status
            )
            assert result == record_mock
            assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_assign_user_no_trigger_when_status_unchanged(self) -> None:
        """Test assign_user does not fire trigger when status unchanged."""
        user_id = uuid4()
        record_mock = MagicMock()
        record_mock.status = RecordStatus.pending
        old_status = RecordStatus.pending

        prefetch_mock = MagicMock()
        prefetch_mock.record_type.unique_per_user = False

        repo_mock = AsyncMock()
        repo_mock.get_with_record_type.return_value = prefetch_mock
        repo_mock.assign_user.return_value = (record_mock, old_status)

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)

        result, result_old_status = await service.assign_user(1, user_id)

        repo_mock.assign_user.assert_awaited_once_with(1, user_id)
        engine_mock.handle_record_status_change.assert_not_awaited()
        assert result == record_mock
        assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_submit_data_fires_status_change_trigger(self) -> None:
        """Test submit_data fires status-change trigger."""
        data = {"field": "value"}
        record_mock = MagicMock()
        record_mock.status = RecordStatus.finished
        old_status = RecordStatus.inwork
        record_read_mock = MagicMock()

        repo_mock = AsyncMock()
        repo_mock.update_data.return_value = (record_mock, old_status)

        engine_mock = AsyncMock()

        service = RecordService(repo_mock, engine_mock)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched,
            patch.object(service, "_emit_output_file_events", new_callable=AsyncMock) as emit_mock,
        ):
            patched.model_validate.return_value = record_read_mock
            result, result_old_status = await service.submit_data(1, data, RecordStatus.finished)

            repo_mock.update_data.assert_awaited_once_with(
                1, data, new_status=RecordStatus.finished
            )
            patched.model_validate.assert_called_once_with(record_mock)
            engine_mock.handle_record_status_change.assert_awaited_once_with(
                record_read_mock, old_status
            )
            emit_mock.assert_awaited_once_with(record_mock)
            assert result == record_mock
            assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_update_data_fires_data_update_trigger(self) -> None:
        """Test update_data fires data-update trigger."""
        data = {"field": "value"}
        record_mock = MagicMock()
        record_mock.status = RecordStatus.pending
        old_status = RecordStatus.pending
        record_read_mock = MagicMock()

        repo_mock = AsyncMock()
        repo_mock.update_data.return_value = (record_mock, old_status)

        engine_mock = AsyncMock()

        service = RecordService(repo_mock, engine_mock)

        with patch("clarinet.services.record_service.RecordRead") as patched:
            patched.model_validate.return_value = record_read_mock
            result, result_old_status = await service.update_data(1, data)

            repo_mock.update_data.assert_awaited_once_with(1, data)
            patched.model_validate.assert_called_once_with(record_mock)
            engine_mock.handle_record_data_update.assert_awaited_once_with(record_read_mock)
            assert result == record_mock
            assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_update_data_no_trigger_when_engine_none(self) -> None:
        """Test update_data does not fire trigger when engine is None."""
        data = {"field": "value"}
        record_mock = MagicMock()
        record_mock.status = RecordStatus.pending
        old_status = RecordStatus.pending

        repo_mock = AsyncMock()
        repo_mock.update_data.return_value = (record_mock, old_status)

        service = RecordService(repo_mock, engine=None)

        result, result_old_status = await service.update_data(1, data)

        repo_mock.update_data.assert_awaited_once_with(1, data)
        assert result == record_mock
        assert result_old_status == old_status

    @pytest.mark.asyncio
    async def test_notify_file_change_fires_file_change_trigger(self) -> None:
        """Test notify_file_change fires file-change trigger."""
        record_mock = MagicMock()
        record_read_mock = MagicMock()

        engine_mock = AsyncMock()
        repo_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)

        with patch("clarinet.services.record_service.RecordRead") as patched:
            patched.model_validate.return_value = record_read_mock
            await service.notify_file_change(record_mock)

            patched.model_validate.assert_called_once_with(record_mock)
            engine_mock.handle_record_file_change.assert_awaited_once_with(record_read_mock)

    @pytest.mark.asyncio
    async def test_notify_file_change_no_trigger_when_engine_none(self) -> None:
        """Test notify_file_change does not fire trigger when engine is None."""
        record_mock = MagicMock()
        repo_mock = AsyncMock()
        service = RecordService(repo_mock, engine=None)

        await service.notify_file_change(record_mock)

    @pytest.mark.asyncio
    async def test_bulk_update_status_fires_per_record_triggers(self) -> None:
        """Test bulk_update_status fires triggers only for changed records."""
        record1_mock = MagicMock()
        record1_mock.id = 1
        record1_mock.status = RecordStatus.pending

        record2_mock = MagicMock()
        record2_mock.id = 2
        record2_mock.status = RecordStatus.pending

        record3_mock = MagicMock()
        record3_mock.id = 3
        record3_mock.status = RecordStatus.inwork  # Already in target status

        updated_record1 = MagicMock()
        updated_record1.status = RecordStatus.inwork

        updated_record2 = MagicMock()
        updated_record2.status = RecordStatus.inwork

        record_read1_mock = MagicMock()
        record_read2_mock = MagicMock()

        repo_mock = AsyncMock()
        repo_mock.get_optional.side_effect = [record1_mock, record2_mock, record3_mock]
        repo_mock.get_with_relations.side_effect = [updated_record1, updated_record2]

        engine_mock = AsyncMock()

        service = RecordService(repo_mock, engine_mock)

        with patch("clarinet.services.record_service.RecordRead") as patched:
            patched.model_validate.side_effect = [record_read1_mock, record_read2_mock]
            await service.bulk_update_status([1, 2, 3], RecordStatus.inwork)

            repo_mock.bulk_update_status.assert_awaited_once_with([1, 2, 3], RecordStatus.inwork)
            assert repo_mock.get_with_relations.await_count == 2
            repo_mock.get_with_relations.assert_any_await(1)
            repo_mock.get_with_relations.assert_any_await(2)
            assert engine_mock.handle_record_status_change.await_count == 2
            engine_mock.handle_record_status_change.assert_any_await(
                record_read1_mock, RecordStatus.pending
            )
            engine_mock.handle_record_status_change.assert_any_await(
                record_read2_mock, RecordStatus.pending
            )

    @pytest.mark.asyncio
    async def test_bulk_update_status_no_trigger_when_engine_none(self) -> None:
        """Test bulk_update_status does not fire triggers when engine is None."""
        record1_mock = MagicMock()
        record1_mock.id = 1
        record1_mock.status = RecordStatus.pending

        updated_record1 = MagicMock()
        updated_record1.status = RecordStatus.inwork

        repo_mock = AsyncMock()
        repo_mock.get_optional.return_value = record1_mock
        repo_mock.get_with_relations.return_value = updated_record1

        service = RecordService(repo_mock, engine=None)

        with patch("clarinet.services.record_service.RecordRead") as patched:
            await service.bulk_update_status([1], RecordStatus.inwork)

            repo_mock.bulk_update_status.assert_awaited_once_with([1], RecordStatus.inwork)
            repo_mock.get_optional.assert_awaited_once_with(1)
            repo_mock.get_with_relations.assert_awaited_once_with(1)
            patched.model_validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_notify_file_updates_fires_per_file_triggers(self) -> None:
        """Test notify_file_updates fires per-file triggers."""
        patient_id = "PAT_001"
        changed_files = ["file1.txt", "file2.txt", "file3.txt"]

        engine_mock = AsyncMock()
        repo_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)

        await service.notify_file_updates(patient_id, changed_files)

        assert engine_mock.handle_file_update.await_count == 3
        engine_mock.handle_file_update.assert_any_await("file1.txt", patient_id, source_record=None)
        engine_mock.handle_file_update.assert_any_await("file2.txt", patient_id, source_record=None)
        engine_mock.handle_file_update.assert_any_await("file3.txt", patient_id, source_record=None)

    @pytest.mark.asyncio
    async def test_notify_file_updates_no_trigger_when_engine_none(self) -> None:
        """Test notify_file_updates does not fire triggers when engine is None."""
        patient_id = "PAT_001"
        changed_files = ["file1.txt", "file2.txt"]

        repo_mock = AsyncMock()
        service = RecordService(repo_mock, engine=None)

        await service.notify_file_updates(patient_id, changed_files)


class TestCreateRecord:
    """Test RecordService.create_record fires RecordFlow triggers."""

    @pytest.mark.asyncio
    async def test_create_record_no_files_fires_trigger(self) -> None:
        """create_record fires status-change trigger with old_status=None."""
        record_mock = MagicMock()
        record_mock.id = 1
        record_mock.parent_record_id = None
        record_read_mock = MagicMock()

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.return_value = record_read_mock
            patched_vrf.return_value = None  # no input files defined

            result = await service.create_record(record_mock)

            repo_mock.create_with_relations.assert_awaited_once_with(record_mock)
            patched_vrf.assert_awaited_once_with(record_read_mock, parent=None)
            engine_mock.handle_record_status_change.assert_awaited_once_with(record_read_mock, None)
            assert result == record_mock

    @pytest.mark.asyncio
    async def test_create_record_valid_files_sets_files(self) -> None:
        """create_record sets matched files when validation passes."""
        record_mock = MagicMock()
        record_mock.id = 1
        record_mock.parent_record_id = None
        refreshed_mock = MagicMock()
        record_read_mock = MagicMock()
        refreshed_read_mock = MagicMock()

        file_result_mock = MagicMock()
        file_result_mock.valid = True
        file_result_mock.matched_files = {"input": "file.nii.gz"}

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        repo_mock.get_with_relations.return_value = refreshed_mock

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.side_effect = [record_read_mock, refreshed_read_mock]
            patched_vrf.return_value = file_result_mock

            result = await service.create_record(record_mock)

            repo_mock.set_files.assert_awaited_once_with(record_mock, {"input": "file.nii.gz"})
            repo_mock.get_with_relations.assert_awaited_once()
            engine_mock.handle_record_status_change.assert_awaited_once_with(
                refreshed_read_mock, None
            )
            assert result == refreshed_mock

    @pytest.mark.asyncio
    async def test_create_record_missing_files_blocks(self) -> None:
        """create_record sets blocked status when required files are missing."""
        record_mock = MagicMock()
        record_mock.id = 1
        record_mock.parent_record_id = None
        blocked_mock = MagicMock()
        blocked_mock.status = RecordStatus.blocked
        record_read_mock = MagicMock()
        blocked_read_mock = MagicMock()

        file_result_mock = MagicMock()
        file_result_mock.valid = False
        file_result_mock.matched_files = {}

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        repo_mock.update_status.return_value = (blocked_mock, RecordStatus.pending)

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.side_effect = [record_read_mock, blocked_read_mock]
            patched_vrf.return_value = file_result_mock

            result = await service.create_record(record_mock)

            repo_mock.update_status.assert_awaited_once_with(1, RecordStatus.blocked)
            engine_mock.handle_record_status_change.assert_awaited_once_with(
                blocked_read_mock, None
            )
            assert result == blocked_mock

    @pytest.mark.asyncio
    async def test_create_record_no_trigger_when_engine_none(self) -> None:
        """create_record does not fire trigger when engine is None."""
        record_mock = MagicMock()
        record_mock.id = 1
        record_mock.parent_record_id = None

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock

        service = RecordService(repo_mock, engine=None)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.return_value = MagicMock()
            patched_vrf.return_value = None

            result = await service.create_record(record_mock)

            repo_mock.create_with_relations.assert_awaited_once_with(record_mock)
            assert result == record_mock


class TestStudyServiceEntityTriggers:
    """Test StudyService entity creation fires RecordFlow triggers."""

    @pytest.mark.asyncio
    async def test_create_patient_fires_entity_created_trigger(self) -> None:
        """Test create_patient commits and fires entity-created trigger."""
        patient_data = {"id": "PAT_001", "name": "Test Patient"}
        patient_mock = MagicMock()
        patient_mock.id = "PAT_001"

        patient_repo_mock = AsyncMock()
        patient_repo_mock.exists.return_value = False
        patient_repo_mock.create.return_value = patient_mock

        study_repo_mock = AsyncMock()
        series_repo_mock = AsyncMock()

        engine_mock = MagicMock()
        engine_mock.handle_entity_created = MagicMock()
        engine_mock.fire = MagicMock()

        service = StudyService(
            study_repo_mock, patient_repo_mock, series_repo_mock, engine=engine_mock
        )

        result = await service.create_patient(patient_data)

        patient_repo_mock.exists.assert_awaited_once_with(id="PAT_001")
        patient_repo_mock.create.assert_awaited_once()
        study_repo_mock.session.commit.assert_awaited_once()
        engine_mock.handle_entity_created.assert_called_once_with("patient", "PAT_001")
        engine_mock.fire.assert_called_once()
        assert result == patient_mock

    @pytest.mark.asyncio
    async def test_create_patient_no_trigger_when_engine_none(self) -> None:
        """Test create_patient does not fire trigger when engine is None."""
        patient_data = {"id": "PAT_001", "name": "Test Patient"}
        patient_mock = MagicMock()
        patient_mock.id = "PAT_001"

        patient_repo_mock = AsyncMock()
        patient_repo_mock.exists.return_value = False
        patient_repo_mock.create.return_value = patient_mock

        study_repo_mock = AsyncMock()
        series_repo_mock = AsyncMock()

        service = StudyService(study_repo_mock, patient_repo_mock, series_repo_mock, engine=None)

        result = await service.create_patient(patient_data)

        patient_repo_mock.exists.assert_awaited_once_with(id="PAT_001")
        patient_repo_mock.create.assert_awaited_once()
        assert result == patient_mock

    @pytest.mark.asyncio
    async def test_create_study_fires_entity_created_trigger(self) -> None:
        """Test create_study commits and fires entity-created trigger."""
        study_data = {
            "study_uid": "1.2.3.4",
            "patient_id": "PAT_001",
            "study_date": "20240101",
        }
        patient_mock = MagicMock()
        patient_mock.id = "PAT_001"

        study_mock = MagicMock()
        study_mock.study_uid = "1.2.3.4"
        study_mock.patient_id = "PAT_001"

        patient_repo_mock = AsyncMock()
        patient_repo_mock.get.return_value = patient_mock

        study_repo_mock = AsyncMock()
        study_repo_mock.exists.return_value = False
        study_repo_mock.create.return_value = study_mock

        series_repo_mock = AsyncMock()

        engine_mock = MagicMock()
        engine_mock.handle_entity_created = MagicMock()
        engine_mock.fire = MagicMock()

        service = StudyService(
            study_repo_mock, patient_repo_mock, series_repo_mock, engine=engine_mock
        )

        result = await service.create_study(study_data)

        patient_repo_mock.get.assert_awaited_once_with("PAT_001")
        study_repo_mock.exists.assert_awaited_once_with(study_uid="1.2.3.4")
        study_repo_mock.create.assert_awaited_once()
        study_repo_mock.session.commit.assert_awaited_once()
        engine_mock.handle_entity_created.assert_called_once_with("study", "PAT_001", "1.2.3.4")
        engine_mock.fire.assert_called_once()
        assert result == study_mock

    @pytest.mark.asyncio
    async def test_create_study_no_trigger_when_engine_none(self) -> None:
        """Test create_study does not fire trigger when engine is None."""
        study_data = {
            "study_uid": "1.2.3.4",
            "patient_id": "PAT_001",
            "study_date": "20240101",
        }
        patient_mock = MagicMock()
        patient_mock.id = "PAT_001"

        study_mock = MagicMock()
        study_mock.study_uid = "1.2.3.4"
        study_mock.patient_id = "PAT_001"

        patient_repo_mock = AsyncMock()
        patient_repo_mock.get.return_value = patient_mock

        study_repo_mock = AsyncMock()
        study_repo_mock.exists.return_value = False
        study_repo_mock.create.return_value = study_mock

        series_repo_mock = AsyncMock()

        service = StudyService(study_repo_mock, patient_repo_mock, series_repo_mock, engine=None)

        result = await service.create_study(study_data)

        patient_repo_mock.get.assert_awaited_once_with("PAT_001")
        study_repo_mock.exists.assert_awaited_once_with(study_uid="1.2.3.4")
        study_repo_mock.create.assert_awaited_once()
        assert result == study_mock

    @pytest.mark.asyncio
    async def test_create_series_fires_entity_created_trigger(self) -> None:
        """Test create_series commits and fires entity-created trigger."""
        series_data = {
            "series_uid": "1.2.3.4.5",
            "study_uid": "1.2.3.4",
            "series_number": 1,
        }
        study_mock = MagicMock()
        study_mock.study_uid = "1.2.3.4"
        study_mock.patient_id = "PAT_001"

        series_mock = MagicMock()
        series_mock.series_uid = "1.2.3.4.5"
        series_mock.study_uid = "1.2.3.4"

        patient_repo_mock = AsyncMock()

        study_repo_mock = AsyncMock()
        study_repo_mock.get.return_value = study_mock

        series_repo_mock = AsyncMock()
        series_repo_mock.exists.return_value = False
        series_repo_mock.create_with_relations.return_value = series_mock

        engine_mock = MagicMock()
        engine_mock.handle_entity_created = MagicMock()
        engine_mock.fire = MagicMock()

        service = StudyService(
            study_repo_mock, patient_repo_mock, series_repo_mock, engine=engine_mock
        )

        result = await service.create_series(series_data)

        study_repo_mock.get.assert_awaited_once_with("1.2.3.4")
        series_repo_mock.exists.assert_awaited_once_with(series_uid="1.2.3.4.5")
        series_repo_mock.create_with_relations.assert_awaited_once()
        study_repo_mock.session.commit.assert_awaited_once()
        engine_mock.handle_entity_created.assert_called_once_with(
            "series", "PAT_001", "1.2.3.4", "1.2.3.4.5"
        )
        engine_mock.fire.assert_called_once()
        assert result == series_mock

    @pytest.mark.asyncio
    async def test_entity_trigger_commits_before_fire(self) -> None:
        """Entity triggers must commit session before firing background task.

        Regression: engine.fire() ran before commit, causing FK violation
        when the background task tried to create a record referencing
        an uncommitted entity.
        """
        study_data = {
            "study_uid": "1.2.3.4",
            "patient_id": "PAT_001",
            "study_date": "20240101",
        }
        patient_mock = MagicMock()
        patient_mock.id = "PAT_001"

        study_mock = MagicMock()
        study_mock.study_uid = "1.2.3.4"
        study_mock.patient_id = "PAT_001"

        patient_repo_mock = AsyncMock()
        patient_repo_mock.get.return_value = patient_mock

        study_repo_mock = AsyncMock()
        study_repo_mock.exists.return_value = False
        study_repo_mock.create.return_value = study_mock

        series_repo_mock = AsyncMock()

        engine_mock = MagicMock()
        engine_mock.handle_entity_created = MagicMock()

        call_order: list[str] = []
        original_commit = study_repo_mock.session.commit

        async def tracking_commit() -> None:
            call_order.append("commit")
            await original_commit()

        engine_mock.fire = lambda coro: call_order.append("fire")
        study_repo_mock.session.commit = tracking_commit

        service = StudyService(
            study_repo_mock, patient_repo_mock, series_repo_mock, engine=engine_mock
        )

        await service.create_study(study_data)

        assert call_order == ["commit", "fire"]

    @pytest.mark.asyncio
    async def test_create_series_no_trigger_when_engine_none(self) -> None:
        """Test create_series does not fire trigger when engine is None."""
        series_data = {
            "series_uid": "1.2.3.4.5",
            "study_uid": "1.2.3.4",
            "series_number": 1,
        }
        study_mock = MagicMock()
        study_mock.study_uid = "1.2.3.4"
        study_mock.patient_id = "PAT_001"

        series_mock = MagicMock()
        series_mock.series_uid = "1.2.3.4.5"
        series_mock.study_uid = "1.2.3.4"

        patient_repo_mock = AsyncMock()

        study_repo_mock = AsyncMock()
        study_repo_mock.get.return_value = study_mock

        series_repo_mock = AsyncMock()
        series_repo_mock.exists.return_value = False
        series_repo_mock.create_with_relations.return_value = series_mock

        service = StudyService(study_repo_mock, patient_repo_mock, series_repo_mock, engine=None)

        result = await service.create_series(series_data)

        study_repo_mock.get.assert_awaited_once_with("1.2.3.4")
        series_repo_mock.exists.assert_awaited_once_with(series_uid="1.2.3.4.5")
        series_repo_mock.create_with_relations.assert_awaited_once()
        assert result == series_mock
