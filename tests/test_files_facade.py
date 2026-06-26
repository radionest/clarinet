import pytest


@pytest.mark.asyncio
async def test_checksum_missing_returns_none(tmp_path):
    from clarinet.files._checksums import compute_file_checksum, checksums_changed
    assert await compute_file_checksum(tmp_path / "nope.bin") is None
    assert checksums_changed({"a": "1"}, {"a": "2", "b": "9"}) == {"a", "b"}


def test_leaf_modules_import():
    from clarinet.files._template import render_template, validate_template, RenderMode
    from clarinet.files._anon import require_anon_or_raw
    from clarinet.files._fs import run_in_fs_thread, shutdown_fs_executor

    assert render_template("{a}", {"a": "x"}) == "x"
    assert validate_template("{patient_id}/{study_uid}/{series_uid}")


def test_storage_render_all_levels_smoke(monkeypatch):
    from pathlib import Path
    from unittest.mock import MagicMock
    from clarinet.models.base import DicomQueryLevel
    from clarinet.files import _storage

    patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    dirs = _storage.render_all_levels(
        patient=patient, study=None, series=None,
        storage_path=Path("/data"), template="{anon_patient_id}/{study_uid}/{series_uid}",
    )
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")


def test_resolver_build_working_dirs(monkeypatch):
    from pathlib import Path
    from unittest.mock import MagicMock
    from clarinet.models.base import DicomQueryLevel
    from clarinet.files import _resolver
    monkeypatch.setattr("clarinet.files._resolver.settings", MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"))

    record = MagicMock()
    record.clarinet_storage_path = None
    record.patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    record.study = None; record.study_uid = None
    record.series = None; record.series_uid = None
    dirs = _resolver.build_working_dirs(record)
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")
