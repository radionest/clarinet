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
