"""Unit tests for RecordService and StudyService RecordFlow triggers."""

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from clarinet.exceptions.domain import UnsafePathError
from clarinet.files import Files
from clarinet.models import RecordStatus
from clarinet.models.actor import SystemActor
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinitionRead, FileRole, RecordFileLinkRead
from clarinet.services.record_service import (
    RecordService,
    _missing_output_links,
    _stored_checksums,
)
from clarinet.services.study_service import StudyService
from clarinet.utils.logger import logger

_MUTATORS = (
    "create_record",
    "update_status",
    "assign_user",
    "claim_record",
    "claim_random_from_pool",
    "unassign_user",
    "submit_data",
    "update_data",
    "bulk_update_status",
    "invalidate_record",
    "fail_record",
    "check_files",
    "update_context_info",
    "clear_output_files",
    "delete_record_cascade",
)


@pytest.mark.parametrize("name", _MUTATORS)
def test_mutators_require_a_keyword_only_actor(name):
    params = inspect.signature(getattr(RecordService, name)).parameters
    actor = params["actor"]
    assert actor.kind is inspect.Parameter.KEYWORD_ONLY
    assert actor.default is inspect.Parameter.empty
    assert not {"acting_user", "actor_id"} & params.keys()


class TestRecordServiceTriggers:
    """Test RecordService mutation methods fire correct RecordFlow triggers."""

    @pytest.mark.asyncio
    async def test_submit_data_with_unsafe_output_pattern_returns_422_before_persisting(
        self,
    ) -> None:
        """The real caller: submit_data must reject a poisoned OUTPUT pattern
        with 422 BEFORE write_transition persists anything, not silently swallow
        the violation into a 200 after the data is already committed."""
        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{patient_id}.nrrd", role=FileRole.OUTPUT)],
            [],
            id=7,
            patient_id="SECRET_MRN_VALUE/../escape",
            status=RecordStatus.pending,
        )

        repo_mock = AsyncMock()
        repo_mock.get_with_relations.return_value = record
        repo_mock.write_transition = AsyncMock(return_value=True)
        repo_mock.get_user_with_roles = AsyncMock(
            return_value=SimpleNamespace(is_superuser=True, role_names=[])
        )
        repo_mock.session.commit = AsyncMock()
        service = RecordService(repo_mock)

        reader_stub = MagicMock()
        reader_stub.dirs.return_value = {DicomQueryLevel.SERIES: Path("/data")}

        with (
            patch("clarinet.services.record_service.RecordRead") as patched,
            patch.object(service, "sync_output_files", new_callable=AsyncMock) as sync_mock,
            patch("clarinet.services.record_service.Files.for_reader", return_value=reader_stub),
        ):
            patched.model_validate.side_effect = lambda r: r
            with pytest.raises(HTTPException) as exc_info:
                await service.submit_data(
                    7,
                    {"field": "value"},
                    RecordStatus.finished,
                    actor=SystemActor(service_user_id=uuid4()),
                )

        assert exc_info.value.status_code == 422
        # Nothing was persisted, and we never even reached the post-commit sync.
        repo_mock.write_transition.assert_not_awaited()
        sync_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_submit_data_allows_collection_output_pattern_with_unsafe_identity_value(
        self,
    ) -> None:
        """Regression for the pre-check over-catching relative to the guard it
        fronts: Files.checksums() globs a multiple=True definition (wildcards
        replace placeholders -- the ``fd.multiple`` branch of Files.checksums
        in files/facade.py) -- it never renders the
        pattern's values at all, so a stored identity value that would fail
        the value guard IF rendered (patient_id="..", legal per
        PATIENT_ID_REGEX but rejected by assert_path_safe_value) must not
        block a submission whose real downstream checksum scan would have
        succeeded. A sibling non-collection, placeholder-bearing OUTPUT def
        is included and is proven -- via the render_for call log, not just
        the absence of a 422 -- to still be rendered: this is not a blanket
        skip of validation, only the collection def is exempted."""
        safe_fd = FileDefinitionRead(name="report", pattern="report_{id}.pdf", role=FileRole.OUTPUT)
        collection_fd = FileDefinitionRead(
            name="masks", pattern="mask_{patient_id}.nrrd", role=FileRole.OUTPUT, multiple=True
        )
        record = _record_read_stub(
            [safe_fd, collection_fd],
            [],
            id=7,
            patient_id="..",
            status=RecordStatus.pending,
        )

        repo_mock = AsyncMock()
        repo_mock.get_with_relations.return_value = record
        repo_mock.write_transition = AsyncMock(return_value=True)
        repo_mock.get_user_with_roles = AsyncMock(
            return_value=SimpleNamespace(is_superuser=True, role_names=[])
        )
        repo_mock.session.commit = AsyncMock()
        service = RecordService(repo_mock)

        reader_stub = MagicMock()
        reader_stub.dirs.return_value = {DicomQueryLevel.SERIES: Path("/data")}

        with (
            patch("clarinet.services.record_service.RecordRead") as patched,
            patch.object(service, "sync_output_files", new_callable=AsyncMock) as sync_mock,
            patch("clarinet.services.record_service.Files.for_reader", return_value=reader_stub),
            patch(
                "clarinet.services.record_service.Files.render_for", wraps=Files.render_for
            ) as render_for_spy,
        ):
            patched.model_validate.side_effect = lambda r: r
            await service.submit_data(
                7,
                {"field": "value"},
                RecordStatus.finished,
                actor=SystemActor(service_user_id=uuid4()),
            )

        repo_mock.write_transition.assert_awaited_once()
        sync_mock.assert_awaited_once()
        # Proof, not inference: the sibling was genuinely rendered (call
        # recorded, with {id} actually substituted), and the collection
        # def's pattern never reached render_for at all.
        rendered_patterns = [call.args[1] for call in render_for_spy.call_args_list]
        assert rendered_patterns == [safe_fd.pattern]
        render_for_spy.assert_called_once_with(record, safe_fd.pattern, parent=None)

    @pytest.mark.asyncio
    async def test_sync_output_files_does_not_swallow_unsafe_path(self) -> None:
        """sync_output_files's checksums() scan is the backstop for a violation
        the pre-submit render-only check cannot see (e.g. a literal pattern that
        only the join/containment check catches). It must surface as 422, not
        get caught by the broad `except Exception` meant for routine I/O
        failures -- that broad except is exactly what let a rejected submission
        through as a silent 200 before this fix."""
        record_mock = MagicMock()
        record_mock.id = 7
        record_mock.parent_record_id = None
        record_mock.record_type.file_registry = [
            FileDefinitionRead(name="seg", pattern="seg.nrrd", role=FileRole.OUTPUT)
        ]

        reader_mock = MagicMock()
        reader_mock.checksums = AsyncMock(side_effect=UnsafePathError("boom", value="../../etc"))

        repo_mock = AsyncMock()
        service = RecordService(repo_mock)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched,
            patch("clarinet.services.record_service.Files") as files_patched,
        ):
            patched.model_validate.return_value = record_mock
            files_patched.for_reader.return_value = reader_mock
            with pytest.raises(HTTPException) as exc_info:
                await service.sync_output_files(record_mock)

        assert exc_info.value.status_code == 422
        assert "boom" in exc_info.value.detail  # str(exc): the reason, not the value
        assert "../../etc" not in exc_info.value.detail  # exc.value is never echoed
        repo_mock.update_checksums.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_output_files_warning_omits_the_value(self, captured_records) -> None:
        """The backstop's WARNING (sync_output_files's new except clause) must
        log only str(exc), never exc.value -- the same PHI contract pinned for
        _render_output_path's WARNING by test_unsafe_placeholder_value_warning_
        omits_the_value. Without this pin, a future edit turning `{exc}` into
        `{exc.value}` at that log line would pass the rest of the suite."""
        record_mock = MagicMock()
        record_mock.id = 7
        record_mock.parent_record_id = None
        record_mock.record_type.file_registry = [
            FileDefinitionRead(name="seg", pattern="seg.nrrd", role=FileRole.OUTPUT)
        ]

        reader_mock = MagicMock()
        reader_mock.checksums = AsyncMock(
            side_effect=UnsafePathError(
                "placeholder {patient_id} resolved to a bare directory reference ('.' or '..')",
                value="SECRET_MRN_VALUE_2",
            )
        )

        repo_mock = AsyncMock()
        service = RecordService(repo_mock)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched,
            patch("clarinet.services.record_service.Files") as files_patched,
        ):
            patched.model_validate.return_value = record_mock
            files_patched.for_reader.return_value = reader_mock
            with pytest.raises(HTTPException):
                await service.sync_output_files(record_mock)

        warnings = [r for r in captured_records if r["level"].name == "WARNING"]
        assert len(warnings) == 1
        message = warnings[0]["message"]
        assert "SECRET_MRN_VALUE_2" not in message
        assert "record 7" in message

    @pytest.mark.asyncio
    async def test_submit_data_degrades_when_working_dirs_raise_anon_path_error(
        self,
    ) -> None:
        """Files.for_reader's own fallback retry can itself raise AnonPathError
        (facade.py:99-101 -- the retry is outside any try, so a second failure
        propagates): a not-yet-anonymized patient whose raw id is ".." fails
        the STRICT attempt for missing anon_id, then fails the FALLBACK
        attempt too, because _storage._safe_render (_storage.py:320-321)
        rejects a rendered ".." segment regardless of the fallback flag. This
        drives the REAL Files.for_reader / _resolver / _storage chain -- no
        stub -- so it is the only test that can see this failure mode.

        The pre-check must degrade to round 1's conservative "validate
        everything" rather than let AnonPathError escape as a 500, and the
        422 must still be reachable for the placeholder-bearing pattern this
        record's OUTPUT definition carries.
        """
        from clarinet.models.record import RecordRead as RealRecordRead

        output_fd = FileDefinitionRead(
            name="seg", pattern="seg_{patient_id}.nrrd", role=FileRole.OUTPUT
        )
        record = MagicMock()
        record.__class__ = RealRecordRead
        record.id = 7
        record.parent_record_id = None
        record.clarinet_storage_path = None
        record.patient_id = ".."
        # Not yet anonymized (anon_id=None) -- the STRICT attempt inside
        # Files.for_reader fails here first, triggering the fallback retry.
        record.patient = MagicMock(id="..", anon_id=None, auto_id=1)
        # Study/series ARE already anonymized, so they resolve cleanly on
        # both attempts -- isolates the failure to the patient segment.
        record.study = MagicMock(study_uid="1.2.3", anon_uid="1.2.3.9")
        record.study_uid = "1.2.3"
        record.series = MagicMock(
            series_uid="1.2.3.4", anon_uid="1.2.3.4.9", modality="CT", series_number=1
        )
        record.series_uid = "1.2.3.4"
        record.record_type = SimpleNamespace(
            name="test_type", level="SERIES", file_registry=[output_fd]
        )
        record.data = {}
        record.file_links = []
        record.status = RecordStatus.pending

        repo_mock = AsyncMock()
        repo_mock.get_with_relations.return_value = record
        repo_mock.write_transition = AsyncMock(return_value=True)
        repo_mock.get_user_with_roles = AsyncMock(
            return_value=SimpleNamespace(is_superuser=True, role_names=[])
        )
        repo_mock.session.commit = AsyncMock()
        service = RecordService(repo_mock)

        with (
            patch("clarinet.services.record_service.RecordRead") as patched,
            patch.object(service, "sync_output_files", new_callable=AsyncMock) as sync_mock,
        ):
            patched.model_validate.side_effect = lambda r: r
            with pytest.raises(HTTPException) as exc_info:
                await service.submit_data(
                    7,
                    {"field": "value"},
                    RecordStatus.finished,
                    actor=SystemActor(service_user_id=uuid4()),
                )

        # The spec-mandated status for a placeholder-bearing pattern rejected
        # by the value guard -- not the 500 an escaped AnonPathError would
        # produce via the ConfigurationError handler.
        assert exc_info.value.status_code == 422
        repo_mock.write_transition.assert_not_awaited()
        sync_mock.assert_not_awaited()

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
        record_mock.status = RecordStatus.pending
        record_mock.user_id = None
        record_read_mock = MagicMock()

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        repo_mock.get_with_relations.return_value = record_mock
        repo_mock.session.commit = AsyncMock()

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)
        actor = SystemActor(service_user_id=uuid4())

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.return_value = record_read_mock
            patched_vrf.return_value = None  # no input files defined

            result = await service.create_record(record_mock, actor=actor)

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
        record_mock.status = RecordStatus.pending
        record_mock.user_id = None
        refreshed_mock = MagicMock()
        record_read_mock = MagicMock()
        refreshed_read_mock = MagicMock()

        file_result_mock = MagicMock()
        file_result_mock.valid = True
        file_result_mock.matched_files = {"input": "file.nii.gz"}

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        repo_mock.get_with_relations.return_value = refreshed_mock
        repo_mock.session.commit = AsyncMock()

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)
        actor = SystemActor(service_user_id=uuid4())

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.side_effect = [record_read_mock, refreshed_read_mock]
            patched_vrf.return_value = file_result_mock

            result = await service.create_record(record_mock, actor=actor)

            repo_mock.set_files.assert_awaited_once_with(
                record_mock, {"input": "file.nii.gz"}, commit=False
            )
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
        record_mock.status = RecordStatus.pending
        record_mock.user_id = None
        blocked_mock = MagicMock()
        blocked_mock.status = RecordStatus.blocked
        blocked_mock.record_type_name = "test-rt"
        record_read_mock = MagicMock()
        blocked_read_mock = MagicMock()

        file_result_mock = MagicMock()
        file_result_mock.valid = False
        file_result_mock.matched_files = {}

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        repo_mock.write_transition.return_value = True
        repo_mock.get_with_relations.return_value = blocked_mock
        repo_mock.session.commit = AsyncMock()

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)
        actor = SystemActor(service_user_id=uuid4())

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.side_effect = [record_read_mock, blocked_read_mock]
            patched_vrf.return_value = file_result_mock

            result = await service.create_record(record_mock, actor=actor)

            repo_mock.write_transition.assert_awaited_once_with(
                1,
                expected_status=RecordStatus.pending,
                expected_user_id=None,
                to=RecordStatus.blocked,
                owner=None,
            )
            engine_mock.handle_record_status_change.assert_awaited_once_with(
                blocked_read_mock, None
            )
            assert result == blocked_mock

    @pytest.mark.asyncio
    async def test_create_record_preparing_skips_auto_block(self) -> None:
        """create_record keeps preparing status even when required files are missing."""
        record_mock = MagicMock()
        record_mock.id = 1
        record_mock.parent_record_id = None
        record_mock.status = RecordStatus.preparing
        record_mock.user_id = None
        record_read_mock = MagicMock()

        file_result_mock = MagicMock()
        file_result_mock.valid = False
        file_result_mock.matched_files = {}

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        repo_mock.get_with_relations.return_value = record_mock
        repo_mock.session.commit = AsyncMock()

        engine_mock = AsyncMock()
        service = RecordService(repo_mock, engine_mock)
        actor = SystemActor(service_user_id=uuid4())

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.return_value = record_read_mock
            patched_vrf.return_value = file_result_mock

            result = await service.create_record(record_mock, actor=actor)

            repo_mock.write_transition.assert_not_awaited()
            engine_mock.handle_record_status_change.assert_awaited_once_with(record_read_mock, None)
            assert result == record_mock

    @pytest.mark.asyncio
    async def test_create_record_no_trigger_when_engine_none(self) -> None:
        """create_record does not fire trigger when engine is None."""
        record_mock = MagicMock()
        record_mock.id = 1
        record_mock.parent_record_id = None
        record_mock.status = RecordStatus.pending
        record_mock.user_id = None

        repo_mock = AsyncMock()
        repo_mock.create_with_relations.return_value = record_mock
        repo_mock.get_with_relations.return_value = record_mock
        repo_mock.session.commit = AsyncMock()

        service = RecordService(repo_mock, engine=None)
        actor = SystemActor(service_user_id=uuid4())

        with (
            patch("clarinet.services.record_service.RecordRead") as patched_read,
            patch("clarinet.services.record_service.validate_record_files") as patched_vrf,
        ):
            patched_read.model_validate.return_value = MagicMock()
            patched_vrf.return_value = None

            result = await service.create_record(record_mock, actor=actor)

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
        patient_repo_mock.find_by_id.return_value = None
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

        patient_repo_mock.find_by_id.assert_awaited_once_with("PAT_001")
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
        patient_repo_mock.find_by_id.return_value = None
        patient_repo_mock.create.return_value = patient_mock

        study_repo_mock = AsyncMock()
        series_repo_mock = AsyncMock()

        service = StudyService(study_repo_mock, patient_repo_mock, series_repo_mock, engine=None)

        result = await service.create_patient(patient_data)

        patient_repo_mock.find_by_id.assert_awaited_once_with("PAT_001")
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


def _record_read_stub(
    file_registry: list[FileDefinitionRead],
    file_links: list[RecordFileLinkRead],
    *,
    level: str = "SERIES",
    **fields: object,
) -> SimpleNamespace:
    """Duck-typed RecordRead stub for _missing_output_links / _stored_checksums tests.

    Provides the minimal duck-typed interface that ``Files.render_for`` (and the
    underlying ``_patterns.fields_from``) requires: ``record_type.name``,
    ``record_type.file_registry``, and all scalar pattern fields.
    ``record_type.level`` (default ``"SERIES"``) is read by
    ``RecordService._validate_output_paths`` to resolve a definition's default
    working-dir level; unused by ``_missing_output_links``/``_stored_checksums``.
    The ``record_type`` role/lock/ownership fields and the top-level
    ``user_id``/``finished_at``/``record_type_name`` let this same stub pass
    through ``RecordService._transition`` (``TypeRules.of`` / ``RecordSnapshot.of``
    / ``_emit_record_updated``) for the submit_data gateway tests.
    """
    return SimpleNamespace(
        record_type=SimpleNamespace(
            name="test_type",
            file_registry=file_registry,
            level=level,
            role_name=None,
            editable=True,
            edit_window_days=None,
            shared_editing=False,
            releasable=False,
        ),
        file_links=file_links,
        # fields_from accesses record.id / record.parent_record_id directly; callers override
        **{
            "id": None,
            "parent_record_id": None,
            "user_id": None,
            "finished_at": None,
            "record_type_name": "test_type",
            "study_uid": None,
            "series_uid": None,
            **fields,
        },
    )


@pytest.fixture
def captured_records():
    """Capture every loguru record emitted during the test as raw dicts.

    Loguru records never reach pytest's `caplog` (that captures the stdlib
    `logging` module only) — mirrors the idiom in tests/test_auth_logging.py.
    """
    records: list[dict] = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    yield records
    logger.remove(sink_id)


class TestMissingOutputLinks:
    """Derivation of missing OUTPUT file links from computed checksums."""

    def test_creates_link_for_unlinked_output(self) -> None:
        record = _record_read_stub(
            [FileDefinitionRead(name="output_mask", pattern="mask.nii.gz", role=FileRole.OUTPUT)],
            [],
        )

        result = _missing_output_links(record, {"output_mask": "abc123"})

        assert result == {"output_mask": "mask.nii.gz"}

    def test_resolves_pattern_placeholders(self) -> None:
        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{id}.nrrd", role=FileRole.OUTPUT)],
            [],
            id=7,
        )

        result = _missing_output_links(record, {"seg": "abc123"})

        assert result == {"seg": "seg_7.nrrd"}

    def test_resolves_placeholder_from_parent_fallback(self) -> None:
        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{user_id}.nrrd", role=FileRole.OUTPUT)],
            [],
        )
        parent = _record_read_stub([], [], user_id="u42")

        result = _missing_output_links(record, {"seg": "abc123"}, parent)

        assert result == {"seg": "seg_u42.nrrd"}

    def test_unresolved_placeholder_stays_empty(self) -> None:
        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{user_id}.nrrd", role=FileRole.OUTPUT)],
            [],
        )

        result = _missing_output_links(record, {"seg": "abc123"})

        assert result == {"seg": "seg_.nrrd"}

    def test_no_anon_uid_does_not_raise(self) -> None:
        """Files.render_for must not raise AnonPathError on a pre-anon record.

        Regression: _missing_output_links used Files(record, parent=parent).render()
        which eagerly built working_dirs and raised AnonPathError when the record
        had no anon_uid. render_for bypasses dir-building entirely.
        """
        from clarinet.files import Files

        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{id}.nrrd", role=FileRole.OUTPUT)],
            [],
            id=42,
        )
        # No anon_uid / anon_patient_id on the stub — simulates pre-anon record.
        result = Files.render_for(record, "seg_{id}.nrrd")
        assert result == "seg_42.nrrd"

        # Also confirm _missing_output_links doesn't raise with the same stub.
        missing = _missing_output_links(record, {"seg": "abc123"})
        assert missing == {"seg": "seg_42.nrrd"}

    def test_collection_takes_first_sorted_file(self) -> None:
        record = _record_read_stub(
            [
                FileDefinitionRead(
                    name="masks", pattern="mask_{id}.nii", role=FileRole.OUTPUT, multiple=True
                )
            ],
            [],
        )

        # Collection keys ("masks:b.nii") make collection_file truthy — render is never called.
        result = _missing_output_links(record, {"masks:b.nii": "1", "masks:a.nii": "2"})

        assert result == {"masks": "a.nii"}

    def test_skips_linked_and_non_output_definitions(self) -> None:
        record = _record_read_stub(
            [
                FileDefinitionRead(name="output_mask", pattern="mask.nii.gz", role=FileRole.OUTPUT),
                FileDefinitionRead(name="input_nifti", pattern="scan.nii.gz", role=FileRole.INPUT),
            ],
            [RecordFileLinkRead(name="output_mask", filename="mask.nii.gz", checksum="old")],
        )

        # Both entries are skipped before render is called: output_mask is already linked,
        # input_nifti has INPUT role — render is never invoked.
        result = _missing_output_links(record, {"output_mask": "abc123", "input_nifti": "def456"})

        assert result == {}

    def test_empty_checksums(self) -> None:
        record = _record_read_stub(
            [FileDefinitionRead(name="output_mask", pattern="mask.nii.gz", role=FileRole.OUTPUT)],
            [],
        )

        assert _missing_output_links(record, {}) == {}

    def test_unsafe_placeholder_value_returns_422(self) -> None:
        """An unsafely-rendering OUTPUT pattern yields a 422, not a 500 — the one
        place an UnsafePathError becomes an HTTPException is the record-submit path.
        {patient_id} is used (not {data.*}) because FileDefinitionRead's own
        pattern validator now rejects {data.*} patterns at construction time;
        the duck-typed stub still lets patient_id carry an unsafe value the
        real Patient regex would never allow, which is exactly the runtime
        case the guard exists for.
        """
        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{patient_id}.nrrd", role=FileRole.OUTPUT)],
            [],
            id=7,
            patient_id="SECRET_MRN_VALUE/../escape",
        )

        with pytest.raises(HTTPException) as exc_info:
            _missing_output_links(record, {"seg": "abc123"})

        assert exc_info.value.status_code == 422
        assert "seg" in exc_info.value.detail  # the file definition is named

    def test_unsafe_placeholder_value_422_omits_the_value(self) -> None:
        """The 422 body must not echo the offending value back to the caller.

        The echo was justified on the grounds that the submitter produced the
        value. With {data.*} banned from *patterns* they did not: fields_from
        still exposes a `data` key, but no pattern may reference it, so every
        name a pattern can interpolate is a stored record attribute. Three of
        those — patient_id, study_uid, series_uid — are what api/masking.py may
        withhold from a non-superuser once the patient is anonymized, while the
        render runs against the raw value. This helper also serves check-files,
        where nothing was submitted at all.
        """
        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{patient_id}.nrrd", role=FileRole.OUTPUT)],
            [],
            id=7,
            patient_id="SECRET_MRN_VALUE/../escape",
        )

        with pytest.raises(HTTPException) as exc_info:
            _missing_output_links(record, {"seg": "abc123"})

        assert "SECRET_MRN_VALUE" not in exc_info.value.detail

    def test_unsafe_placeholder_value_warning_omits_the_value(self, captured_records) -> None:
        """record.data may carry PHI — the WARNING must name the record and the
        file definition without ever repeating the offending value."""
        record = _record_read_stub(
            [FileDefinitionRead(name="seg", pattern="seg_{patient_id}.nrrd", role=FileRole.OUTPUT)],
            [],
            id=7,
            patient_id="SECRET_MRN_VALUE/../escape",
        )

        with pytest.raises(HTTPException):
            _missing_output_links(record, {"seg": "abc123"})

        warnings = [r for r in captured_records if r["level"].name == "WARNING"]
        assert len(warnings) == 1
        message = warnings[0]["message"]
        assert "SECRET_MRN_VALUE" not in message
        assert "record 7" in message
        assert "'seg'" in message
        assert "patient_id" in message


class TestStoredChecksums:
    """Stored link checksums are keyed to match compute_checksums output."""

    def test_emits_both_singular_and_collection_keys(self) -> None:
        record = _record_read_stub(
            [],
            [RecordFileLinkRead(name="masks", filename="a.nii", checksum="X")],
        )

        assert _stored_checksums(record) == {"masks": "X", "masks:a.nii": "X"}

    def test_skips_links_without_checksum(self) -> None:
        record = _record_read_stub(
            [],
            [RecordFileLinkRead(name="masks", filename="a.nii")],
        )

        assert _stored_checksums(record) == {}
