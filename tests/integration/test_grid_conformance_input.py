"""Declared INPUT grid mismatches block a record and release on repair.

Proves that Task 4's runtime grid check (``FileValidator.validate`` /
``FileValidationError(error_type="grid_mismatch")``) is inherited for free by
every record-lifecycle seam that already calls ``validate_record_files``:
creation (``RecordService.create_record``), auto-unblock
(``RecordService.check_files``), the report-only ``/validate-files``
endpoint, and the explicit ``preparing`` -> ``pending`` exit
(``RecordService._resolve_preparing_exit``). This module adds no production
code — if any test here fails, a seam stopped inheriting the check and that
must be fixed before OUTPUT-side enforcement (Task 6) is built on top of it.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest_asyncio

from clarinet.files import Files
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.file_schema import FileDefinition, FileRole, RecordTypeFileLink
from clarinet.models.patient import Patient
from clarinet.models.record import Record, RecordType
from clarinet.models.study import Series, Study
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.image.image import FileType, Image
from clarinet.services.record_service import RecordService
from clarinet.settings import settings
from tests.utils.urls import record_validate_files_url

_Z_FLIP = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]])


def _write(path: Path, *, direction=None, origin=(0.0, 0.0, 0.0), shape=(6, 6, 6)):
    """Write a tiny NIfTI volume with a controllable grid; returns its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image()
    img.spacing = (1.0, 1.0, 1.0)
    img.origin = origin
    img.direction = np.eye(3) if direction is None else direction
    img.img = np.zeros(shape, dtype=np.uint8)
    return img.save_as(path, FileType.NIFTI)


def _record(record_type_name: str, series: SimpleNamespace, **kwargs) -> Record:
    """Build an unsaved SERIES-level Record for *series*'s patient/study/series."""
    return Record(
        record_type_name=record_type_name,
        patient_id=series.patient_id,
        study_uid=series.study_uid,
        series_uid=series.series_uid,
        **kwargs,
    )


async def _seg_record_type(
    test_session, name: str, *, on_grid_mismatch: str | None = None
) -> RecordType:
    """SERIES-level type: volume (INPUT) + seg (INPUT, conforms to volume)."""
    volume = FileDefinition(name="volume", pattern="volume.nii", level=DicomQueryLevel.SERIES)
    seg = FileDefinition(
        name="seg",
        pattern="seg.nii",
        level=DicomQueryLevel.SERIES,
        grid_conform_to="volume",
        on_grid_mismatch=on_grid_mismatch,
    )
    test_session.add_all([volume, seg])
    await test_session.flush()

    rt = RecordType(name=name, level=DicomQueryLevel.SERIES)
    test_session.add(rt)
    await test_session.flush()
    test_session.add_all(
        [
            RecordTypeFileLink(
                record_type_name=rt.name,
                file_definition_id=volume.id,
                role=FileRole.INPUT,
                required=True,
            ),
            RecordTypeFileLink(
                record_type_name=rt.name,
                file_definition_id=seg.id,
                role=FileRole.INPUT,
                required=True,
            ),
        ]
    )
    await test_session.commit()
    return rt


@pytest_asyncio.fixture
async def seg_record_type(test_session) -> RecordType:
    """SERIES-level type: volume (INPUT) + seg (INPUT, conforms to volume)."""
    return await _seg_record_type(test_session, "grid-input-task")


@pytest_asyncio.fixture
async def seg_record_type_conform(test_session) -> RecordType:
    """Same shape as ``seg_record_type``, but ``seg.on_grid_mismatch="conform"``.

    ``seg`` stays bound as ``FileRole.INPUT`` — proves the action is
    consulted only for OUTPUT files (see ``GridMismatchAction`` docstring).
    """
    return await _seg_record_type(
        test_session, "grid-input-conform-task", on_grid_mismatch="conform"
    )


@pytest_asyncio.fixture
async def patient_with_anon(test_session) -> Patient:
    """Patient with auto_id set so anon_id resolves to a deterministic path segment."""
    patient = Patient(
        id="PAT_GRID_IN", name="Grid Input Patient", anon_name="ANON_GRID_IN", auto_id=910
    )
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def study_with_anon(test_session, patient_with_anon) -> Study:
    """Study with anon_uid set."""
    study = Study(
        patient_id=patient_with_anon.id,
        study_uid="1.2.840.10008.9.9.1",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_STUDY_GRID_IN",
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)
    return study


@pytest_asyncio.fixture
async def series_with_anon(test_session, study_with_anon) -> Series:
    """Series with anon_uid set."""
    series = Series(
        study_uid=study_with_anon.study_uid,
        series_uid="1.2.840.10008.9.9.1.1",
        series_number=1,
        series_description="Grid Input Series",
        anon_uid="ANON_SERIES_GRID_IN",
    )
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)
    return series


@pytest_asyncio.fixture
async def series(patient_with_anon, study_with_anon, series_with_anon) -> SimpleNamespace:
    """Bundled patient/study/series identifiers for building a ``Record(...)``.

    Not the ORM ``Series`` row itself (which has no ``patient_id``) — just
    the three DICOM-level keys every test needs to construct a record.
    """
    return SimpleNamespace(
        patient_id=patient_with_anon.id,
        study_uid=study_with_anon.study_uid,
        series_uid=series_with_anon.series_uid,
    )


@pytest_asyncio.fixture
async def series_dir(
    patient_with_anon, study_with_anon, series_with_anon, tmp_path, monkeypatch
) -> Path:
    """Working directory the record's files resolve into (SERIES level).

    Points ``settings.storage_path`` at *tmp_path* and renders the same
    ``disk_path_template`` the record-lifecycle seams use, so every test can
    write files here before any record exists.
    """
    monkeypatch.setattr(settings, "storage_path", str(tmp_path))
    dirs = Files.working_dirs(
        patient=patient_with_anon, study=study_with_anon, series=series_with_anon
    )
    return dirs[DicomQueryLevel.SERIES]


# ===========================================================================
# Creation-time blocking (RecordService.create_record)
# ===========================================================================


async def test_mismatched_input_blocks_record_on_creation(
    test_session, seg_record_type, series_dir, series
):
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))

    service = RecordService(RecordRepository(test_session), engine=None)
    record = await service.create_record(_record(seg_record_type.name, series))
    assert record.status == RecordStatus.blocked


async def test_conforming_inputs_do_not_block(test_session, seg_record_type, series_dir, series):
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii")

    service = RecordService(RecordRepository(test_session), engine=None)
    record = await service.create_record(_record(seg_record_type.name, series))
    assert record.status != RecordStatus.blocked


# ===========================================================================
# Release on repair (RecordService.check_files)
# ===========================================================================


async def test_check_files_unblocks_after_repair(test_session, seg_record_type, series_dir, series):
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))

    service = RecordService(RecordRepository(test_session), engine=None)
    record = await service.create_record(_record(seg_record_type.name, series))
    assert record.status == RecordStatus.blocked

    _write(series_dir / "seg.nii")  # repaired onto the volume's grid
    await service.check_files(record.id)

    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    assert refreshed.status == RecordStatus.pending


async def test_check_files_leaves_record_blocked_while_mismatched(
    test_session, seg_record_type, series_dir, series
):
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))

    service = RecordService(RecordRepository(test_session), engine=None)
    record = await service.create_record(_record(seg_record_type.name, series))

    assert await service.check_files(record.id) == ([], {})
    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    assert refreshed.status == RecordStatus.blocked


# ===========================================================================
# Report-only endpoint (POST /records/{id}/validate-files)
# ===========================================================================


async def test_validate_files_reports_mismatch_without_mutating(
    client, test_session, seg_record_type, series_dir, series
):
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before_bytes = seg_path.read_bytes()

    service = RecordService(RecordRepository(test_session), engine=None)
    record = await service.create_record(_record(seg_record_type.name, series))

    response = await client.post(record_validate_files_url(record.id))
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert any(e["error_type"] == "grid_mismatch" for e in body["errors"])
    assert seg_path.read_bytes() == before_bytes


# ===========================================================================
# Policy never touches an input (on_grid_mismatch is OUTPUT-only)
# ===========================================================================


async def test_conform_action_never_repairs_an_input(
    test_session, seg_record_type_conform, series_dir, series
):
    """on_grid_mismatch is OUTPUT-only — an INPUT is never rewritten or deleted."""
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()

    service = RecordService(RecordRepository(test_session), engine=None)
    record = await service.create_record(_record(seg_record_type_conform.name, series))

    assert record.status == RecordStatus.blocked
    assert seg_path.read_bytes() == before  # untouched


# ===========================================================================
# Third seam: explicit preparing -> pending exit re-validation
# ===========================================================================


async def test_preparing_to_pending_blocks_on_grid_mismatch(
    test_session, seg_record_type, series_dir, series
):
    """RecordService._resolve_preparing_exit also inherits the grid check.

    Not named in the plan, but it is a third live seam through
    ``validate_record_files``: creation-time blocking is skipped for
    ``preparing`` records, and files are re-validated on the explicit exit
    to ``pending`` instead.
    """
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))

    service = RecordService(RecordRepository(test_session), engine=None)
    record = await service.create_record(
        _record(seg_record_type.name, series, status=RecordStatus.preparing)
    )
    assert record.status == RecordStatus.preparing  # creation-time blocking is skipped

    updated, old_status = await service.update_status(record.id, RecordStatus.pending)
    assert old_status == RecordStatus.preparing
    assert updated.status == RecordStatus.blocked
