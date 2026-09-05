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

from clarinet.exceptions.domain import BusinessRuleViolationError
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
from tests.utils.urls import record_data_url, record_submit_url, record_validate_files_url

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


async def test_reject_409_includes_both_grid_summaries(
    client, test_session, series_dir, make_record
):
    """#499 asked for '409 with both grid summaries' — the INPUT side had them,
    the OUTPUT side reported only the ``RelationKind``.
    """
    volume_path = _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 409
    detail = str(response.json())
    assert read_grid(seg_path).summary() in detail
    assert read_grid(volume_path).summary() in detail


async def test_output_grid_refusal_carries_a_machine_readable_code(
    client, test_session, series_dir, make_record
):
    """The 409 body carries ``code: GRID_MISMATCH`` so a client can branch on
    it instead of parsing the detail text. (The Gleam client reads ``code`` on
    its 409 path; its 422 arm does not yet — #573.)
    """
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 409
    assert response.json()["code"] == "GRID_MISMATCH"


async def test_input_grid_mismatch_at_submit_carries_the_same_code(
    client, test_session, series_dir, make_record
):
    """An INPUT pair that drifted while the record sat pending is caught by the
    submit-time re-validation as a 422, not by the OUTPUT guard's 409 — the
    same ``code`` on both lets a client handle them uniformly.
    """
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch=None, seg_role=FileRole.INPUT)

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 422
    assert response.json()["code"] == "GRID_MISMATCH"


async def test_missing_input_422_carries_no_grid_code(
    client, test_session, series_dir, make_record
):
    """A plain missing required input is not a grid problem: its 422 keeps the
    bare ``{"detail"}`` shape, so a client branching on the code never
    mistakes it for a mismatch.
    """
    _write(series_dir / "volume.nii")
    record = await make_record(on_grid_mismatch=None, extra_required_input="report")

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 422
    assert "code" not in response.json()


async def test_failed_conform_repair_read_preserves_original_bytes(
    client, test_session, series_dir, make_record, monkeypatch
):
    """A repair whose result cannot even be read must leave the original intact
    (pre-fix, the repair overwrote the subject in place before the recheck).
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()

    def _bad_repair(seg, grid, *, out_path=None, **kwargs):
        Path(out_path).write_bytes(b"garbage")
        return True

    monkeypatch.setattr("clarinet.services.grid_policy.conform_seg_to_grid", _bad_repair)
    record = await make_record(on_grid_mismatch="conform")

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 409
    assert seg_path.read_bytes() == before
    assert not list(series_dir.glob(".repair.*"))


async def test_failed_conform_recheck_preserves_original_bytes(
    client, test_session, series_dir, make_record, monkeypatch
):
    """A repair that lands on a readable-but-still-wrong grid must 409 with the
    original untouched and the temp file cleaned up.
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()

    def _wrong_repair(seg, grid, *, out_path=None, **kwargs):
        _write(Path(out_path), shape=(8, 8, 8))  # valid file, FOREIGN grid
        return True

    monkeypatch.setattr("clarinet.services.grid_policy.conform_seg_to_grid", _wrong_repair)
    record = await make_record(on_grid_mismatch="conform")

    response = await client.post(record_submit_url(record.id), json={})

    assert response.status_code == 409
    assert seg_path.read_bytes() == before
    assert not list(series_dir.glob(".repair.*"))


async def test_crashed_conform_repair_leaves_no_temp_file(
    client, test_session, series_dir, make_record, monkeypatch
):
    """A writer failure outside ``ImageError`` — an unwrapped reader error, a
    ``MemoryError`` on a large volume — is still a server fault (500), but it
    must not strand the hidden temp file: ``Path.glob`` matches dotfiles, so
    an orphan is counted by any overlapping collection pattern on every
    check-files run. The ASGI test transport re-raises what the 500 handler
    wrapped, hence ``pytest.raises`` rather than a status assertion.
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()

    def _crashing_repair(seg, grid, *, out_path=None, **kwargs):
        Path(out_path).write_bytes(b"partial")
        raise RuntimeError("writer died mid-write")

    monkeypatch.setattr("clarinet.services.grid_policy.conform_seg_to_grid", _crashing_repair)
    record = await make_record(on_grid_mismatch="conform")

    with pytest.raises(RuntimeError, match="mid-write"):
        await client.post(record_submit_url(record.id), json={})

    assert seg_path.read_bytes() == before
    assert not list(series_dir.glob(".repair.*"))


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
# Read-only preview: POST /records/{id}/validate-files reports OUTPUT pairs
# ===========================================================================


async def test_validate_files_reports_output_mismatch_without_mutating(
    client, test_session, series_dir, make_record
):
    """validate-files is the read-only preview of the submit guard: it names
    the OUTPUT pair and quotes the declared action, and touches nothing —
    even with ``delete`` declared, which a submit would carry out.
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()
    record = await make_record(on_grid_mismatch="delete")

    response = await client.post(record_validate_files_url(record.id))

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    [error] = [e for e in body["errors"] if e["file_name"] == "seg"]
    assert error["error_type"] == "grid_mismatch"
    assert "on_grid_mismatch=delete" in error["message"]
    assert seg_path.read_bytes() == before


async def test_validate_files_does_not_report_an_absent_output(
    client, test_session, series_dir, make_record
):
    """An OUTPUT not written yet is not "missing" — the submit guard skips it
    too — so the report stays valid.
    """
    _write(series_dir / "volume.nii")
    record = await make_record(on_grid_mismatch="reject")

    response = await client.post(record_validate_files_url(record.id))

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["errors"] == []


async def test_validate_files_does_not_report_a_repairable_conform_pair(
    client, test_session, series_dir, make_record
):
    """A REARRANGED uint8 pair under ``conform`` is repaired at submit and
    passes (matrix row ``("conform", "rearranged", 200, ...)``), so the preview
    must not announce a 409 for it: the report follows ``decide()``, not the
    raw mismatch — and previews without repairing.
    """
    _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    before = seg_path.read_bytes()
    record = await make_record(on_grid_mismatch="conform")

    response = await client.post(record_validate_files_url(record.id))

    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["errors"] == []
    assert seg_path.read_bytes() == before


async def test_validate_files_reports_an_unrepairable_conform_pair_with_its_reason(
    client, test_session, series_dir, make_record
):
    """``conform`` on a wider-than-8-bit subject is a REJECT at submit; the
    preview says so and names the dtype rule, as the 409 would.
    """
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0), dtype=np.int16)
    record = await make_record(on_grid_mismatch="conform")

    response = await client.post(record_validate_files_url(record.id))

    body = response.json()
    assert body["valid"] is False
    [error] = [e for e in body["errors"] if e["file_name"] == "seg"]
    assert "8-bit" in error["message"]
    assert "on_grid_mismatch=conform" in error["message"]


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
        record_data_url(record.id), json={"note": "just editing metadata"}
    )

    assert response.status_code == 409
    assert not seg_path.exists()
    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    assert (refreshed.data or {}) == before_data


async def test_patch_data_conform_repair_syncs_checksum(
    client, test_session, series_dir, make_record
):
    """The POST path proves checksum-after-conform consistency; before this fix
    the PATCH path repaired the bytes but left the stored checksum stale.
    """
    volume_path = _write(series_dir / "volume.nii")
    seg_path = _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))
    record = await make_record(on_grid_mismatch="conform", status=RecordStatus.finished)

    response = await client.patch(record_data_url(record.id), json={"note": "edit"})

    assert response.status_code == 200
    relation = grid_relation(read_grid(volume_path), read_grid(seg_path))
    assert relation.kind is RelationKind.SAME
    refreshed = await RecordRepository(test_session).get_with_relations(record.id)
    stored = {link.file_definition.name: link.checksum for link in refreshed.file_links}
    assert stored["seg"] == await Files.checksum(Path(seg_path))


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
        f"{record_data_url(record.id)}?status=failed", json={"note": "task failed"}
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


async def test_unbound_reference_is_a_conflict_at_runtime(test_session, series_dir, make_record):
    """The runtime fallback 409 for a dangling ``grid_conform_to`` had no test —
    only ``FileValidator``'s twin was covered.
    """
    _write(series_dir / "volume.nii")
    _write(series_dir / "seg.nii", direction=_Z_FLIP, origin=(0.0, 0.0, 5.0))

    record = await make_record(on_grid_mismatch="reject")
    loaded = await RecordRepository(test_session).get_with_relations(record.id)
    record_read = RecordRead.model_validate(loaded)
    record_read.record_type.file_registry = [
        fd for fd in (record_read.record_type.file_registry or []) if fd.name == "seg"
    ]

    with pytest.raises(BusinessRuleViolationError, match="not bound"):
        await enforce_output_grids(record_read)
