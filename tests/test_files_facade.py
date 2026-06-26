import pytest
from pathlib import Path
from unittest.mock import MagicMock
from clarinet.models.base import DicomQueryLevel


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


def _record(monkeypatch, *, registry=None, level="SERIES"):
    monkeypatch.setattr(
        "clarinet.files._resolver.settings",
        MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"),
    )
    r = MagicMock()
    r.clarinet_storage_path = None
    r.id = 7; r.user_id = "u1"; r.patient_id = "P1"
    r.patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    r.study = MagicMock(study_uid="S", anon_uid="S"); r.study_uid = "S"
    r.series = MagicMock(series_uid="SE", anon_uid="SE", modality="CT", series_number=1); r.series_uid = "SE"
    r.record_type = MagicMock(level=level, file_registry=registry or [])
    r.record_type.name = "seg"
    r.data = {}
    # make isinstance(r, RecordRead) true:
    from clarinet.models.record import RecordRead
    r.__class__ = RecordRead
    return r


def test_files_dir_and_dirs(monkeypatch):
    from clarinet.files.facade import Files
    f = Files(_record(monkeypatch))
    assert f.dir() == Path("/data/CLARINET_1/S/SE")
    assert f.dir(DicomQueryLevel.PATIENT) == Path("/data/CLARINET_1")
    assert set(f.dirs()) == {DicomQueryLevel.PATIENT, DicomQueryLevel.STUDY, DicomQueryLevel.SERIES}


def test_files_rejects_bad_type():
    from clarinet.files.facade import Files
    with pytest.raises(TypeError):
        Files(object())


def test_files_empty():
    from clarinet.files.facade import Files
    assert Files.empty().dirs() == {}


def test_files_resolve(monkeypatch):
    from clarinet.files.facade import Files
    fd = MagicMock(name="fd"); fd.name = "seg"; fd.pattern = "seg_{id}.nrrd"; fd.level = None
    f = Files(_record(monkeypatch, registry=[fd]))
    assert f.resolve("seg") == Path("/data/CLARINET_1/S/SE/seg_7.nrrd")
    assert f.accessed["seg"] == Path("/data/CLARINET_1/S/SE/seg_7.nrrd")


def test_files_render_uses_unified_engine(monkeypatch):
    from clarinet.files.facade import Files
    rec = _record(monkeypatch)
    rec.data = {"mods": ["SR", "CT"]}
    f = Files(rec)
    assert f.render("{data.mods}_{id}") == "CT_SR_7"  # type-aware list coercion


def test_files_render_template_strict_raises():
    from clarinet.files.facade import Files
    with pytest.raises(KeyError):
        Files.render_template("{missing}", {}, strict=True)
    assert Files.render_template("{missing}", {}) == ""


@pytest.mark.asyncio
async def test_files_checksums_omits_missing(monkeypatch):
    from clarinet.files.facade import Files
    fd = MagicMock(); fd.name = "seg"; fd.pattern = "seg_{id}.nrrd"; fd.level = None; fd.multiple = False
    f = Files(_record(monkeypatch, registry=[fd]))
    assert await f.checksums() == {}  # file does not exist on disk → omitted


def test_files_working_dirs_classmethod(monkeypatch):
    from clarinet.files.facade import Files
    monkeypatch.setattr("clarinet.files.facade.settings", MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"))
    monkeypatch.setattr("clarinet.files._storage.settings", MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"))
    patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    dirs = Files.working_dirs(patient=patient, study=None, series=None, template="{anon_patient_id}/{study_uid}/{series_uid}")
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")
