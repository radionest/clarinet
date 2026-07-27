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

import numpy as np
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
from tests.utils.urls import RECORDS_BASE, record_submit_url

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
    persisted record bound to the shared patient/study/series.

    ``seg_role`` defaults to ``OUTPUT`` (what every decision-table case
    exercises); the one caller needing an ``INPUT`` binding passes it
    explicitly — see ``test_enforce_output_grids_skips_input_role``.
    ``status`` defaults to ``inwork`` (submit-seam cases); the ``/data``
    coverage passes ``finished`` directly since ``PATCH /data`` requires it.
    ``extra_required_input``, when given a name, binds one more required
    INPUT with no grid declaration of its own — for tests pinning the
    INPUT-before-OUTPUT ordering (Finding 2), never written to disk so it is
    always "missing".
    """

    async def _make(
        *,
        on_grid_mismatch: str | None,
        seg_role: FileRole = FileRole.OUTPUT,
        status: RecordStatus = RecordStatus.inwork,
        extra_required_input: str | None = None,
    ) -> Record:
        volume = FileDefinition(name="volume", pattern="volume.nii", level=DicomQueryLevel.SERIES)
        seg = FileDefinition(
            name="seg",
            pattern="seg.nii",
            level=DicomQueryLevel.SERIES,
            grid_conform_to="volume",
            on_grid_mismatch=on_grid_mismatch,
        )
        defs = [volume, seg]
        extra: FileDefinition | None = None
        if extra_required_input:
            extra = FileDefinition(name=extra_required_input, pattern=f"{extra_required_input}.txt")
            defs.append(extra)
        test_session.add_all(defs)
        await test_session.flush()

        rt = RecordType(name="grid-submit-task", level=DicomQueryLevel.SERIES)
        test_session.add(rt)
        await test_session.flush()
        links = [
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
        if extra is not None:
            links.append(
                RecordTypeFileLink(
                    record_type_name=rt.name,
                    file_definition_id=extra.id,
                    role=FileRole.INPUT,
                    required=True,
                )
            )
        test_session.add_all(links)
        await test_session.commit()

        record = Record(
            record_type_name=rt.name,
            patient_id=patient_with_anon.id,
            study_uid=study_with_anon.study_uid,
            series_uid=series_with_anon.series_uid,
            status=status,
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
    before = seg_path.read_bytes()

    record = await make_record(on_grid_mismatch=action)
    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == expected_status
    assert seg_path.exists() is survives
    if repaired:
        relation = grid_relation(read_grid(volume_path), read_grid(seg_path))
        assert relation.kind is RelationKind.SAME
    elif survives:
        # "untouched", not just "still exists" — a silent rewrite would pass
        # the exists() check above but must not pass this one.
        assert seg_path.read_bytes() == before


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
    assert refreshed.status == original.status
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


async def test_conform_refuses_to_quantize_a_non_uint8_output(
    client, test_session, series_dir, make_record
):
    """``conform`` must fail closed on a wider-than-uint8 OUTPUT (Finding 1).

    ``conform_seg_to_grid``'s non-layered repair path reads the subject
    through ``Segmentation``, which forces a uint8 cast — silently wrapping
    a resampled CT (int16 HU) or any float volume. A registered OUTPUT that
    isn't already 8-bit on disk must be refused (409, file untouched) rather
    than quantized and silently accepted.
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(
        series_dir / "seg.nii",
        direction=_Z_FLIP,
        origin=(0.0, 0.0, 5.0),
        dtype=np.int16,
    )
    before = seg_path.read_bytes()
    record = await make_record(on_grid_mismatch="conform")

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 409
    assert seg_path.read_bytes() == before


async def test_missing_required_input_takes_priority_over_output_delete(
    client, test_session, series_dir, make_record
):
    """422 (missing input), not 409 (destructive OUTPUT delete) — Finding 2.

    The destructive OUTPUT action must never run before the non-destructive
    INPUT check: a submission invalid on both counts must surface the
    diagnostic 422 and leave the mismatched OUTPUT alone, not delete it and
    report a 409 that has nothing to do with the real problem.
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()
    # "report.txt" is required but never written to disk.
    record = await make_record(on_grid_mismatch="delete", extra_required_input="report")
    before_status, before_data = record.status, dict(record.data or {})

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 422
    assert seg_path.read_bytes() == before
    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    assert refreshed.status == before_status
    assert (refreshed.data or {}) == before_data


async def test_missing_grid_reference_is_shadowed_by_missing_required_input(
    client, test_session, series_dir, make_record
):
    """On POST, a missing grid reference that is also a required INPUT
    surfaces as 422 (missing input) per Finding 2's ordering — not
    ``grid_policy.py``'s own "reference not on disk" 409.

    ``volume`` plays both roles here (declared grid reference *and* required
    INPUT), so once it's missing the INPUT check answers first and the OUTPUT
    guard never runs. That branch is isolated from the INPUT check (and
    still directly exercised) via PATCH below, where inputs are never
    re-validated.
    """
    _write(series_dir / "seg.nii")  # no volume.nii at all
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(record_submit_url(record.id), json={})
    assert response.status_code == 422


async def test_patch_submit_reports_missing_grid_reference_as_a_conflict(
    client, test_session, series_dir, make_record
):
    """``grid_policy.py``'s ``if not reference.is_file():`` branch, isolated
    from the INPUT check via PATCH (which never re-validates inputs) — the
    scenario ``test_missing_grid_reference_is_shadowed_by_missing_required_input``
    can no longer isolate on POST now that inputs are checked first.
    """
    volume_path = _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii")
    record = await make_record(on_grid_mismatch="reject")
    assert (await client.post(record_submit_url(record.id), json={})).status_code == 200

    volume_path.unlink()  # the reference disappears after the record was submitted

    response = await client.patch(record_submit_url(record.id), json={"note": "resubmit"})
    assert response.status_code == 409
    assert "not on disk" in response.text


async def test_unreadable_output_is_a_conflict_not_a_500(
    client, test_session, series_dir, make_record
):
    """A present-but-corrupt OUTPUT (e.g. a truncated Slicer write) must map to
    409, not an unhandled 500. ``ImageError`` is not registered in
    ``exception_handlers.py``, so ``enforce_output_grids`` has to contain it.
    """
    _write(series_dir / "volume.nii")
    (series_dir / "seg.nii").write_bytes(b"not a valid nifti file")
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(record_submit_url(record.id), json={})
    assert response.status_code == 409


# ===========================================================================
# The same seam, reached through POST/PATCH /data instead of /submit
# ===========================================================================
#
# _process_submission backs four routes, not two: POST/PATCH /data default
# to status=finished, so a plain "save my data" call is functionally a
# submission too, and the guard is deliberately not gated off of it — a
# fail-open metadata route would be the same hole this change closes.


async def test_patch_data_applies_delete_action(client, test_session, series_dir, make_record):
    """``PATCH /data`` is guarded identically to ``/submit`` — including
    ``delete``. Accepted hazard, not a bug: this is a metadata-only edit
    from the caller's point of view, but the design applies the declared
    action uniformly across every ``_process_submission`` caller, so a
    ``REARRANGED`` OUTPUT under ``on_grid_mismatch="delete"`` is destroyed
    here exactly like it would be on ``/submit``. Documented so nobody
    "fixes" this by accident later.
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch="delete", status=RecordStatus.finished)
    before_data = dict(record.data or {})

    response = await client.patch(
        f"{RECORDS_BASE}/{record.id}/data", json={"note": "just editing metadata"}
    )

    assert response.status_code == 409
    assert not seg_path.exists()
    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    assert (refreshed.data or {}) == before_data


async def test_post_data_status_failed_skips_the_guard(
    client, test_session, series_dir, make_record
):
    """``POST /data?status=failed`` is the one caller that can set
    ``skip_validation=True`` (``not is_update and new_status != finished``) —
    ``POST /submit`` has no ``status`` query param and always submits
    finished, so this branch is reachable only here. A mismatched, undeclared-
    action OUTPUT must not block a failure report.
    """
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(
        f"{RECORDS_BASE}/{record.id}/data?status=failed", json={"note": "task failed"}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "failed"


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
