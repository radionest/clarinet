"""Submit-time enforcement of declared OUTPUT grid conformance.

OUTPUT files are never validated by ``validate_record_files`` (it filters to
``role == INPUT``), and the resubmit path (``PATCH /submit``) skipped file
validation entirely before this module's production code
(``clarinet.services.grid_policy.enforce_output_grids``) existed. Re-opening a
legacy record, re-painting, and re-exporting mirrored is the highest mirror
risk in the system — this module closes that gap for both ``POST`` and
``PATCH /submit``.
"""

from pathlib import Path

import pytest
import pytest_asyncio

from clarinet.files import Files
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.file_schema import FileDefinition, FileRole, RecordTypeFileLink
from clarinet.models.record import Record, RecordRead, RecordType
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.grid_policy import enforce_output_grids
from clarinet.services.image.grid import RelationKind, grid_relation
from clarinet.services.image.grid_io import read_grid
from tests.integration.test_grid_conformance_input import (
    patient_with_anon as patient_with_anon,
)
from tests.integration.test_grid_conformance_input import (
    series_dir as series_dir,
)
from tests.integration.test_grid_conformance_input import (
    series_with_anon as series_with_anon,
)
from tests.integration.test_grid_conformance_input import (
    study_with_anon as study_with_anon,
)
from tests.utils.test_helpers import Z_FLIP as _Z_FLIP
from tests.utils.test_helpers import write_grid_image as _write
from tests.utils.urls import record_submit_url

# (action, mismatch kind, expected status, file must survive, file must be repaired)
_ACTION_MATRIX = [
    ("reject", "rearranged", 409, True, False),
    ("reject", "foreign", 409, True, False),
    ("conform", "rearranged", 200, True, True),
    ("conform", "foreign", 409, True, False),
    ("delete", "rearranged", 409, False, False),
    ("delete", "foreign", 409, False, False),
    (None, "rearranged", 409, True, False),  # unset defaults to reject
    (None, "foreign", 409, True, False),
]


@pytest_asyncio.fixture
async def make_record(test_session, patient_with_anon, study_with_anon, series_with_anon):
    """Factory: a record type (``volume`` INPUT + ``seg`` OUTPUT, ``seg`` conforming
    to ``volume``) built with the given ``on_grid_mismatch`` action, then a
    persisted ``inwork`` record bound to the shared patient/study/series.

    ``seg_role`` defaults to ``OUTPUT`` (what every decision-table case
    exercises); the one caller needing an ``INPUT`` binding passes it
    explicitly — see ``test_enforce_output_grids_skips_input_role``.
    """

    async def _make(
        *, on_grid_mismatch: str | None, seg_role: FileRole = FileRole.OUTPUT
    ) -> Record:
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

        rt = RecordType(name="grid-submit-task", level=DicomQueryLevel.SERIES)
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
                    role=seg_role,
                    required=False,
                ),
            ]
        )
        await test_session.commit()

        record = Record(
            record_type_name=rt.name,
            patient_id=patient_with_anon.id,
            study_uid=study_with_anon.study_uid,
            series_uid=series_with_anon.series_uid,
            status=RecordStatus.inwork,
        )
        test_session.add(record)
        await test_session.commit()
        await test_session.refresh(record)
        return record

    return _make


# ===========================================================================
# Decision table (design D8): action x mismatch kind -> status/survival/repair
# ===========================================================================


@pytest.mark.parametrize("action,kind,expected_status,survives,repaired", _ACTION_MATRIX)
async def test_output_action_matrix(
    client,
    test_session,
    series_dir,
    make_record,
    action,
    kind,
    expected_status,
    survives,
    repaired,
):
    volume_path = _write(series_dir / "volume.nii")
    if kind == "rearranged":
        seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    else:  # foreign — different shape is unrepairable
        seg_path = _write(series_dir / "seg.nii", shape=(8, 8, 8))

    record = await make_record(on_grid_mismatch=action)
    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == expected_status
    assert seg_path.exists() is survives
    if repaired:
        relation = grid_relation(read_grid(volume_path), read_grid(seg_path))
        assert relation.kind is RelationKind.SAME


# ===========================================================================
# Standalone cases
# ===========================================================================


async def test_rejected_submission_persists_nothing(client, test_session, series_dir, make_record):
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch="reject")
    before_status, before_data = record.status, dict(record.data or {})

    response = await client.post(record_submit_url(record.id), json={"note": "should not persist"})

    assert response.status_code == 409
    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    assert refreshed.status == before_status
    assert (refreshed.data or {}) == before_data


async def test_conforming_output_submits_normally(client, test_session, series_dir, make_record):
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii")  # same grid
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(record_submit_url(record.id), json={})
    assert response.status_code == 200


async def test_absent_output_does_not_block_submission(
    client, test_session, series_dir, make_record
):
    _write(series_dir / "volume.nii")  # no seg.nii at all
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(record_submit_url(record.id), json={})
    assert response.status_code == 200


async def test_patch_submit_is_guarded(client, test_session, series_dir, make_record):
    """The resubmit path validated nothing before this change."""
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii")
    record = await make_record(on_grid_mismatch="reject")
    assert (await client.post(record_submit_url(record.id), json={})).status_code == 200

    original = await RecordRepository(test_session).get_with_relations(record.id)
    original_data = dict(original.data or {})
    # the re-export lands mirrored
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))

    response = await client.patch(record_submit_url(record.id), json={"note": "reworked"})
    assert response.status_code == 409
    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    assert (refreshed.data or {}) == original_data


async def test_conform_repair_checksum_matches_repaired_bytes(
    client, test_session, series_dir, make_record
):
    """One consistent file-change event: the post-commit scan sees final bytes."""
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch="conform")

    assert (await client.post(record_submit_url(record.id), json={})).status_code == 200

    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    stored = {link.file_definition.name: link.checksum for link in refreshed.file_links}
    assert stored["seg"] == await Files.checksum(Path(seg_path))


# ===========================================================================
# Task 5 forward-guard follow-up
# ===========================================================================
#
# test_grid_conformance_input.py::test_conform_action_never_repairs_an_input
# proves an INPUT file survives record *creation* — a path that never calls
# enforce_output_grids at all, so it cannot exercise this function's own role
# filter. The test below calls enforce_output_grids directly against a
# registry that binds the mismatched file as INPUT: if the ``fd.role ==
# FileRole.OUTPUT`` condition in ``declared`` ever regressed to also match
# INPUT, this would either repair or reject the file — this test fails
# either way, unlike the creation-time test which would stay green regardless.


async def test_enforce_output_grids_skips_input_role(test_session, series_dir, make_record):
    """``enforce_output_grids`` only ever dispatches on OUTPUT-role files."""
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()

    record = await make_record(on_grid_mismatch="conform", seg_role=FileRole.INPUT)
    loaded = await RecordRepository(test_session).get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)

    await enforce_output_grids(record_read)  # must not raise — INPUT is out of scope

    assert seg_path.read_bytes() == before  # untouched
