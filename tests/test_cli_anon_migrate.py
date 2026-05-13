"""Integration tests for ``clarinet anon migrate-paths`` CLI command."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.cli.anon import _cleanup_empty_dirs, migrate_paths
from clarinet.models.base import DicomQueryLevel
from clarinet.models.record import Record
from clarinet.models.study import Series, Study
from clarinet.services.dicom.anon_path import build_context, render_working_folder
from tests.utils.factories import make_patient, make_record_type
from tests.utils.session import PassThroughSession


async def _seed_anonymized_series(
    test_session: AsyncSession,
    *,
    patient_id: str,
    auto_id: int,
    study_uid: str,
    series_uid: str,
    anon_study_uid: str,
    anon_series_uid: str,
) -> tuple[object, Study, Series]:
    patient = make_patient(patient_id, "Migrate Test", auto_id=auto_id)
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient_id,
        study_uid=study_uid,
        date=datetime.now(UTC).date(),
        modalities_in_study="CT",
        anon_uid=anon_study_uid,
    )
    test_session.add(study)
    await test_session.commit()

    series = Series(
        study_uid=study_uid,
        series_uid=series_uid,
        series_number=1,
        modality="CT",
        anon_uid=anon_series_uid,
    )
    test_session.add(series)
    await test_session.commit()
    return patient, study, series


def _populate_old_path(storage_path: Path, patient, study, series, old_template: str) -> Path:
    """Render old path and create a dummy ``foo.dcm`` inside ``dcm_anon``."""
    ctx = build_context(patient=patient, study=study, series=series)
    series_dir = render_working_folder(old_template, DicomQueryLevel.SERIES, ctx, storage_path)
    dcm_anon = series_dir / "dcm_anon"
    dcm_anon.mkdir(parents=True, exist_ok=True)
    (dcm_anon / "foo.dcm").write_bytes(b"DICOM-placeholder")
    return dcm_anon


async def _run_migrate(
    args: argparse.Namespace,
    test_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """Invoke ``migrate_paths`` with the standard test mocks."""
    with (
        patch("clarinet.cli.anon.db_manager") as mock_dbm,
        patch("clarinet.cli.anon.settings") as mock_settings,
    ):
        mock_settings.storage_path = str(tmp_path)
        mock_dbm.async_session_factory = lambda: PassThroughSession(test_session)
        await migrate_paths(args)


@pytest.mark.asyncio
async def test_migrate_paths_dry_run_moves_nothing(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """--dry-run reports the plan without touching the filesystem."""
    old_template = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_template = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_DRY_01",
        auto_id=101,
        study_uid="1.2.6001.1",
        series_uid="1.2.6001.1.1",
        anon_study_uid="2.25.601",
        anon_series_uid="2.25.601.1",
    )

    old_dcm_anon = _populate_old_path(tmp_path, patient, study, series, old_template)

    args = argparse.Namespace(
        from_template=old_template,
        to_template=new_template,
        dry_run=True,
        cleanup_empty=False,
    )

    await _run_migrate(args, test_session, tmp_path)

    assert old_dcm_anon.is_dir()
    assert (old_dcm_anon / "foo.dcm").exists()


@pytest.mark.asyncio
async def test_migrate_paths_moves_files(test_session: AsyncSession, tmp_path: Path) -> None:
    """Without --dry-run, dcm_anon directories are moved from old to new template."""
    old_template = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_template = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_RUN_01",
        auto_id=202,
        study_uid="1.2.6002.1",
        series_uid="1.2.6002.1.1",
        anon_study_uid="2.25.602",
        anon_series_uid="2.25.602.1",
    )

    old_dcm_anon = _populate_old_path(tmp_path, patient, study, series, old_template)
    assert old_dcm_anon.is_dir()

    args = argparse.Namespace(
        from_template=old_template,
        to_template=new_template,
        dry_run=False,
        cleanup_empty=False,
    )

    await _run_migrate(args, test_session, tmp_path)

    # New path layout
    ctx = build_context(patient=patient, study=study, series=series)
    new_series_dir = render_working_folder(new_template, DicomQueryLevel.SERIES, ctx, tmp_path)
    new_dcm_anon = new_series_dir / "dcm_anon"

    assert new_dcm_anon.is_dir(), f"New dcm_anon missing at {new_dcm_anon}"
    assert (new_dcm_anon / "foo.dcm").exists()
    # Old location no longer exists
    assert not old_dcm_anon.is_dir()


@pytest.mark.asyncio
async def test_migrate_paths_identical_template_is_noop(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    template = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    args = argparse.Namespace(
        from_template=template,
        to_template=template,
        dry_run=False,
        cleanup_empty=False,
    )
    # Should not raise even with no DB / files setup beyond what's already there.
    await _run_migrate(args, test_session, tmp_path)


@pytest.mark.asyncio
async def test_migrate_paths_rejects_invalid_template(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    args = argparse.Namespace(
        from_template="{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
        to_template="{patient_auto_id}/{not_a_field}/{anon_series_uid}",
        dry_run=False,
        cleanup_empty=False,
    )
    with pytest.raises(ValueError, match="unknown placeholder"):
        await _run_migrate(args, test_session, tmp_path)


@pytest.mark.asyncio
async def test_migrate_paths_cleanup_empty_leaves_unrelated_dirs(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """``--cleanup-empty`` only walks up from migrated paths, leaving
    other empty directories under ``storage_path`` alone."""
    old_template = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_template = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_CLEAN_01",
        auto_id=303,
        study_uid="1.2.6003.1",
        series_uid="1.2.6003.1.1",
        anon_study_uid="2.25.603",
        anon_series_uid="2.25.603.1",
    )
    old_dcm_anon = _populate_old_path(tmp_path, patient, study, series, old_template)

    # Stray empty dir under storage_path that has nothing to do with the migration
    stray_dir = tmp_path / "stray" / "subdir"
    stray_dir.mkdir(parents=True)

    args = argparse.Namespace(
        from_template=old_template,
        to_template=new_template,
        dry_run=False,
        cleanup_empty=True,
    )
    await _run_migrate(args, test_session, tmp_path)

    # Old series dir is gone — walked up and removed
    assert not old_dcm_anon.is_dir()
    assert not old_dcm_anon.parent.is_dir()  # series_uid level pruned
    # Stray dir is untouched
    assert stray_dir.is_dir()


def test_cleanup_empty_dirs_stops_at_root(tmp_path: Path) -> None:
    """``_cleanup_empty_dirs`` does not delete or escape past ``stop_at``."""
    inner = tmp_path / "a" / "b" / "c"
    inner.mkdir(parents=True)
    sibling = tmp_path / "a" / "sibling"
    sibling.mkdir()

    removed = _cleanup_empty_dirs([inner], stop_at=tmp_path)

    # c, b are removed; a stays because sibling is non-empty (still exists);
    # tmp_path itself never touched.
    assert removed == 2
    assert not inner.exists()
    assert not (tmp_path / "a" / "b").exists()
    assert (tmp_path / "a").exists()  # sibling pinned it
    assert sibling.exists()
    assert tmp_path.exists()


def test_cleanup_empty_dirs_refuses_to_escape(tmp_path: Path) -> None:
    """A root outside ``stop_at`` is silently skipped, not walked."""
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    try:
        removed = _cleanup_empty_dirs([outside], stop_at=tmp_path)
        assert removed == 0
        assert outside.exists()
    finally:
        outside.rmdir()


# ---------------------------------------------------------------------------
# --include-working-folder mode
# ---------------------------------------------------------------------------


def _populate_series_dir_full(
    storage_path: Path, patient, study, series, old_template: str
) -> tuple[Path, Path]:
    """Populate old series_dir with both ``dcm_anon/foo.dcm`` and a pipeline output."""
    ctx = build_context(patient=patient, study=study, series=series)
    series_dir = render_working_folder(old_template, DicomQueryLevel.SERIES, ctx, storage_path)
    dcm_anon = series_dir / "dcm_anon"
    dcm_anon.mkdir(parents=True, exist_ok=True)
    (dcm_anon / "foo.dcm").write_bytes(b"DICOM-placeholder")
    (series_dir / "volume.nii.gz").write_bytes(b"NIFTI-placeholder")
    return series_dir, dcm_anon


def _populate_level_dir(
    storage_path: Path,
    patient,
    study,
    series,
    template: str,
    level: DicomQueryLevel,
    filename: str = "artifact.bin",
) -> Path:
    """Populate a STUDY/PATIENT-level dir with a single file. Returns the file path."""
    ctx = build_context(patient=patient, study=study, series=series)
    level_dir = render_working_folder(template, level, ctx, storage_path)
    level_dir.mkdir(parents=True, exist_ok=True)
    artifact = level_dir / filename
    artifact.write_bytes(b"x")
    return artifact


async def _seed_record_at_level(
    test_session: AsyncSession,
    *,
    rt_name: str,
    level: DicomQueryLevel,
    patient_id: str,
    study_uid: str | None,
    series_uid: str | None,
) -> Record:
    """Persist a RecordType at ``level`` and a matching Record bound to it."""
    rt = make_record_type(name=rt_name, level=level)
    test_session.add(rt)
    await test_session.commit()
    rec = Record(
        patient_id=patient_id,
        study_uid=study_uid,
        series_uid=series_uid,
        record_type_name=rt_name,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)
    return rec


@pytest.mark.asyncio
async def test_include_working_folder_moves_full_series_dir(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """With the flag, the whole series_dir (incl. pipeline outputs) is moved."""
    old_tmpl = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_tmpl = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_WF_01",
        auto_id=401,
        study_uid="1.2.6401.1",
        series_uid="1.2.6401.1.1",
        anon_study_uid="2.25.641",
        anon_series_uid="2.25.641.1",
    )
    old_series_dir, old_dcm_anon = _populate_series_dir_full(
        tmp_path, patient, study, series, old_tmpl
    )
    assert (old_series_dir / "volume.nii.gz").exists()

    args = argparse.Namespace(
        from_template=old_tmpl,
        to_template=new_tmpl,
        dry_run=False,
        cleanup_empty=False,
        include_working_folder=True,
    )
    await _run_migrate(args, test_session, tmp_path)

    ctx = build_context(patient=patient, study=study, series=series)
    new_series_dir = render_working_folder(new_tmpl, DicomQueryLevel.SERIES, ctx, tmp_path)
    assert new_series_dir.is_dir()
    assert (new_series_dir / "dcm_anon" / "foo.dcm").exists()
    assert (new_series_dir / "volume.nii.gz").exists()
    assert not old_series_dir.exists()
    assert not old_dcm_anon.exists()


@pytest.mark.asyncio
async def test_include_working_folder_dry_run_does_not_move(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """``--dry-run`` with the flag leaves the filesystem untouched."""
    old_tmpl = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_tmpl = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_WF_DRY",
        auto_id=402,
        study_uid="1.2.6402.1",
        series_uid="1.2.6402.1.1",
        anon_study_uid="2.25.642",
        anon_series_uid="2.25.642.1",
    )
    old_series_dir, _ = _populate_series_dir_full(tmp_path, patient, study, series, old_tmpl)

    args = argparse.Namespace(
        from_template=old_tmpl,
        to_template=new_tmpl,
        dry_run=True,
        cleanup_empty=False,
        include_working_folder=True,
    )
    await _run_migrate(args, test_session, tmp_path)

    assert old_series_dir.is_dir()
    assert (old_series_dir / "volume.nii.gz").exists()


@pytest.mark.asyncio
async def test_include_working_folder_study_record_moves_study_files(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """STUDY-level Record moves study-level loose files to the new study_dir."""
    old_tmpl = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_tmpl = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_WF_STUDY",
        auto_id=403,
        study_uid="1.2.6403.1",
        series_uid="1.2.6403.1.1",
        anon_study_uid="2.25.643",
        anon_series_uid="2.25.643.1",
    )
    await _seed_record_at_level(
        test_session,
        rt_name="rt-study-403",
        level=DicomQueryLevel.STUDY,
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=None,
    )
    _populate_series_dir_full(tmp_path, patient, study, series, old_tmpl)
    old_study_artifact = _populate_level_dir(
        tmp_path,
        patient,
        study,
        series,
        old_tmpl,
        DicomQueryLevel.STUDY,
        filename="segmentation.seg.nrrd",
    )

    args = argparse.Namespace(
        from_template=old_tmpl,
        to_template=new_tmpl,
        dry_run=False,
        cleanup_empty=False,
        include_working_folder=True,
    )
    await _run_migrate(args, test_session, tmp_path)

    ctx = build_context(patient=patient, study=study, series=series)
    new_study_dir = render_working_folder(new_tmpl, DicomQueryLevel.STUDY, ctx, tmp_path)
    assert (new_study_dir / "segmentation.seg.nrrd").exists()
    assert not old_study_artifact.exists()


@pytest.mark.asyncio
async def test_include_working_folder_patient_record_moves_patient_files(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """PATIENT-level Record moves patient-level loose files to the new patient_dir."""
    old_tmpl = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_tmpl = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_WF_PAT",
        auto_id=404,
        study_uid="1.2.6404.1",
        series_uid="1.2.6404.1.1",
        anon_study_uid="2.25.644",
        anon_series_uid="2.25.644.1",
    )
    await _seed_record_at_level(
        test_session,
        rt_name="rt-pat-404",
        level=DicomQueryLevel.PATIENT,
        patient_id=patient.id,
        study_uid=None,
        series_uid=None,
    )
    _populate_series_dir_full(tmp_path, patient, study, series, old_tmpl)
    old_patient_artifact = _populate_level_dir(
        tmp_path,
        patient,
        study,
        series,
        old_tmpl,
        DicomQueryLevel.PATIENT,
        filename="liver.nii.gz",
    )

    args = argparse.Namespace(
        from_template=old_tmpl,
        to_template=new_tmpl,
        dry_run=False,
        cleanup_empty=False,
        include_working_folder=True,
    )
    await _run_migrate(args, test_session, tmp_path)

    ctx = build_context(patient=patient, study=study, series=series)
    new_patient_dir = render_working_folder(new_tmpl, DicomQueryLevel.PATIENT, ctx, tmp_path)
    assert (new_patient_dir / "liver.nii.gz").exists()
    assert not old_patient_artifact.exists()


@pytest.mark.asyncio
async def test_include_working_folder_series_collision_skips(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """If the new series_dir already exists, the whole record is skipped."""
    old_tmpl = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_tmpl = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_WF_COLL",
        auto_id=405,
        study_uid="1.2.6405.1",
        series_uid="1.2.6405.1.1",
        anon_study_uid="2.25.645",
        anon_series_uid="2.25.645.1",
    )
    old_series_dir, _ = _populate_series_dir_full(tmp_path, patient, study, series, old_tmpl)

    # Pre-create the target as if a previous run already placed something there.
    ctx = build_context(patient=patient, study=study, series=series)
    new_series_dir = render_working_folder(new_tmpl, DicomQueryLevel.SERIES, ctx, tmp_path)
    new_series_dir.mkdir(parents=True)
    (new_series_dir / "preexisting.txt").write_bytes(b"already here")

    args = argparse.Namespace(
        from_template=old_tmpl,
        to_template=new_tmpl,
        dry_run=False,
        cleanup_empty=False,
        include_working_folder=True,
    )
    await _run_migrate(args, test_session, tmp_path)

    # Source untouched; pre-existing target preserved; no merging into it.
    assert old_series_dir.is_dir()
    assert (old_series_dir / "volume.nii.gz").exists()
    assert (new_series_dir / "preexisting.txt").exists()
    assert not (new_series_dir / "volume.nii.gz").exists()


@pytest.mark.asyncio
async def test_dcm_anon_default_mode_collision_skips(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """Without the flag: pre-existing target dcm_anon → skip (defends against
    silent ``shutil.move`` nesting of source inside an existing target dir)."""
    old_tmpl = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_tmpl = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_DCM_COLL",
        auto_id=406,
        study_uid="1.2.6406.1",
        series_uid="1.2.6406.1.1",
        anon_study_uid="2.25.646",
        anon_series_uid="2.25.646.1",
    )
    old_dcm_anon = _populate_old_path(tmp_path, patient, study, series, old_tmpl)

    # Pre-create the target dcm_anon to trigger collision.
    ctx = build_context(patient=patient, study=study, series=series)
    new_series_dir = render_working_folder(new_tmpl, DicomQueryLevel.SERIES, ctx, tmp_path)
    new_dcm_anon = new_series_dir / "dcm_anon"
    new_dcm_anon.mkdir(parents=True)
    (new_dcm_anon / "preexisting.dcm").write_bytes(b"already here")

    args = argparse.Namespace(
        from_template=old_tmpl,
        to_template=new_tmpl,
        dry_run=False,
        cleanup_empty=False,
    )
    await _run_migrate(args, test_session, tmp_path)

    # Source preserved, target untouched — NOT nested inside (silent bug check).
    assert old_dcm_anon.is_dir()
    assert (old_dcm_anon / "foo.dcm").exists()
    assert (new_dcm_anon / "preexisting.dcm").exists()
    assert not (new_dcm_anon / "dcm_anon").exists()  # no nesting
    assert not (new_dcm_anon / "foo.dcm").exists()  # no merge


@pytest.mark.asyncio
async def test_include_working_folder_cleanup_prunes_empty_parents(
    test_session: AsyncSession, tmp_path: Path
) -> None:
    """``--cleanup-empty`` removes old study_dir/patient_dir that the SERIES
    pass emptied — even when STUDY/PATIENT Records render to ``same`` outcome
    because their old dirs no longer contain any loose files."""
    old_tmpl = "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}"
    new_tmpl = "{patient_auto_id}/{study_modalities}_{study_date}/{anon_series_uid}"

    patient, study, series = await _seed_anonymized_series(
        test_session,
        patient_id="MIG_WF_CLEAN",
        auto_id=407,
        study_uid="1.2.6407.1",
        series_uid="1.2.6407.1.1",
        anon_study_uid="2.25.647",
        anon_series_uid="2.25.647.1",
    )
    # STUDY and PATIENT Records exist but no loose files under their dirs —
    # only the series_dir below has content. After SERIES pass, study_dir
    # and patient_dir become empty; the cleanup pass must prune them.
    await _seed_record_at_level(
        test_session,
        rt_name="rt-study-407",
        level=DicomQueryLevel.STUDY,
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=None,
    )
    await _seed_record_at_level(
        test_session,
        rt_name="rt-pat-407",
        level=DicomQueryLevel.PATIENT,
        patient_id=patient.id,
        study_uid=None,
        series_uid=None,
    )
    old_series_dir, _ = _populate_series_dir_full(tmp_path, patient, study, series, old_tmpl)
    old_study_dir = old_series_dir.parent
    old_patient_dir = old_study_dir.parent

    args = argparse.Namespace(
        from_template=old_tmpl,
        to_template=new_tmpl,
        dry_run=False,
        cleanup_empty=True,
        include_working_folder=True,
    )
    await _run_migrate(args, test_session, tmp_path)

    assert not old_series_dir.exists()
    assert not old_study_dir.exists()  # pruned via ``same`` cleanup branch
    assert not old_patient_dir.exists()
