import hashlib
from pathlib import Path

import pytest

from clarinet.services.pipeline import fingerprint as fp


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


def test_queue_version_segment_format() -> None:
    fp.reset_fingerprint_cache()
    seg = fp.queue_version_segment()
    assert len(seg) == 12
    assert all(c in "0123456789abcdef" for c in seg)


def test_clarinet_version_nonempty() -> None:
    assert fp.clarinet_version()
