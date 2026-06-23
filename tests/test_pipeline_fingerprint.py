import hashlib
from pathlib import Path

import pytest

from clarinet.services.pipeline import fingerprint as fp
from clarinet.settings import settings


@pytest.fixture
def plan_dir(tmp_path: Path) -> Path:
    (tmp_path / "tasks.py").write_text("x = 1\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "flow.py").write_text("y = 2\n")
    return tmp_path


def test_compute_plan_hash_deterministic(plan_dir: Path) -> None:
    assert fp.compute_plan_hash(plan_dir) == fp.compute_plan_hash(plan_dir)


def test_compute_plan_hash_sensitive_to_content(plan_dir: Path) -> None:
    before = fp.compute_plan_hash(plan_dir)
    (plan_dir / "tasks.py").write_text("x = 2\n")
    assert fp.compute_plan_hash(plan_dir) != before


def test_compute_plan_hash_ignores_artifacts(plan_dir: Path) -> None:
    before = fp.compute_plan_hash(plan_dir)
    cache = plan_dir / "__pycache__"
    cache.mkdir()
    (cache / "tasks.cpython-312.pyc").write_bytes(b"\x00\x01")
    (plan_dir / "debug.log").write_text("noise\n")
    (plan_dir / ".DS_Store").write_bytes(b"\x00")
    assert fp.compute_plan_hash(plan_dir) == before


def test_compute_plan_hash_missing_root(tmp_path: Path) -> None:
    assert fp.compute_plan_hash(tmp_path / "nope") == hashlib.sha256().hexdigest()


def test_compute_plan_hash_normalizes_line_endings(tmp_path: Path) -> None:
    # A CRLF (Windows/git autocrlf) checkout must hash identically to LF, so a
    # Linux API and a Windows worker on the same code agree on queue names.
    lf = tmp_path / "lf"
    lf.mkdir()
    (lf / "a.py").write_bytes(b"x = 1\ny = 2\n")
    crlf = tmp_path / "crlf"
    crlf.mkdir()
    (crlf / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    assert fp.compute_plan_hash(lf) == fp.compute_plan_hash(crlf)


def test_compute_plan_hash_ignores_host_local(plan_dir: Path) -> None:
    before = fp.compute_plan_hash(plan_dir)
    (plan_dir / ".env").write_text("SECRET=1\n")
    (plan_dir / "tasks.py~").write_text("editor backup\n")
    (plan_dir / ".idea").mkdir()
    (plan_dir / ".idea" / "workspace.xml").write_text("<x/>\n")
    assert fp.compute_plan_hash(plan_dir) == before


def test_queue_version_segment_format() -> None:
    fp.reset_fingerprint_cache()
    seg = fp.queue_version_segment()
    assert len(seg) == 12
    assert all(c in "0123456789abcdef" for c in seg)


def test_clarinet_version_nonempty() -> None:
    assert fp.clarinet_version()


def test_queue_name_versioned_when_enabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "config_tasks_path", str(tmp_path))
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", True)
    fp.reset_fingerprint_cache()
    seg = fp.queue_version_segment()
    ns = settings.pipeline_task_namespace
    assert settings.default_queue_name == f"{ns}.{seg}.default"
    assert settings.gpu_queue_name == f"{ns}.{seg}.gpu"
    assert settings.dicom_queue_name == f"{ns}.{seg}.dicom"
    assert settings.quarto_queue_name == f"{ns}.{seg}.quarto"


def test_queue_name_legacy_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", False)
    ns = settings.pipeline_task_namespace
    assert settings.default_queue_name == f"{ns}.default"


def test_dlq_never_versioned(monkeypatch) -> None:
    monkeypatch.setattr(settings, "pipeline_version_check_enabled", True)
    fp.reset_fingerprint_cache()
    ns = settings.pipeline_task_namespace
    assert settings.dlq_queue_name == f"{ns}.dead_letter"
