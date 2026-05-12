"""Integration tests for ``clarinet anon migrate-paths`` CLI command."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.cli.anon import migrate_paths
from clarinet.models.base import DicomQueryLevel
from clarinet.models.study import Series, Study
from clarinet.services.dicom.anon_path import build_context, render_working_folder
from tests.utils.factories import make_patient


@pytest.fixture
def anon_series(test_session: AsyncSession) -> None:
    """Skeleton fixture — empty, real setup happens per-test (different patient ids)."""


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

    with (
        patch("clarinet.cli.anon.db_manager") as mock_dbm,
        patch("clarinet.cli.anon.settings") as mock_settings,
    ):
        mock_settings.storage_path = str(tmp_path)
        mock_dbm.async_session_factory = lambda: _PassThroughSession(test_session)
        await migrate_paths(args)

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

    with (
        patch("clarinet.cli.anon.db_manager") as mock_dbm,
        patch("clarinet.cli.anon.settings") as mock_settings,
    ):
        mock_settings.storage_path = str(tmp_path)
        mock_dbm.async_session_factory = lambda: _PassThroughSession(test_session)
        await migrate_paths(args)

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
    with (
        patch("clarinet.cli.anon.db_manager") as mock_dbm,
        patch("clarinet.cli.anon.settings") as mock_settings,
    ):
        mock_settings.storage_path = str(tmp_path)
        mock_dbm.async_session_factory = lambda: _PassThroughSession(test_session)
        await migrate_paths(args)


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
    with (
        patch("clarinet.cli.anon.db_manager") as mock_dbm,
        patch("clarinet.cli.anon.settings") as mock_settings,
        pytest.raises(ValueError, match="unknown placeholder"),
    ):
        mock_settings.storage_path = str(tmp_path)
        mock_dbm.async_session_factory = lambda: _PassThroughSession(test_session)
        await migrate_paths(args)


class _PassThroughSession:
    """Adapter: wraps an existing AsyncSession to be used as ``async with``.

    The migration CLI does ``async with db_manager.async_session_factory() as
    session``; in tests we already have a session injected via the
    ``test_session`` fixture, so we just yield it back and skip close/commit.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc) -> None:
        return None
