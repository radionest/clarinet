"""The packaged project-scaffold payload and the write-once copy (`clarinet init`)."""

import os
import re
import stat
import tomllib
from pathlib import Path

import pytest

import clarinet
from clarinet.exceptions.domain import ProjectScaffoldError
from clarinet.utils import project_scaffold
from clarinet.utils.project_scaffold import scaffold_project, scaffold_source_dir

PACKAGE_ROOT = Path(clarinet.__file__).resolve().parent

EXPECTED_PAYLOAD = [
    "settings.toml",
    "settings.custom.toml",
    "env.example",
    "gitignore",
    "plan/slicer_hydrators.py",
    "plan/definitions/record_types.py",
    "plan/workflows/pipeline_flow.py",
    "plan/validators/example_validator.py",
    "plan/schemas/_common.schema.json",
    "plan/schemas/first-check.schema.json",
    "plan/scripts/example.py",
    "plan/utils/__init__.py",
]


def test_payload_lives_in_the_package_and_is_complete() -> None:
    src = scaffold_source_dir()
    assert src.parent == PACKAGE_ROOT  # #472: must ship in the wheel
    missing = [rel for rel in EXPECTED_PAYLOAD if not (src / rel).is_file()]
    assert not missing, f"payload files missing: {missing}"
    assert not list(src.rglob(".*")), (
        "no dotfiles in the payload — a .gitignore there governs the wheel"
    )
    assert not (src / ".claude").exists()


def test_real_payload_scaffolds_with_dotted_names(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    assert scaffold_project(project) == []
    dotted = {"gitignore": ".gitignore", "env.example": ".env.example"}
    expected = {dotted.get(rel, rel) for rel in EXPECTED_PAYLOAD}
    written = {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}
    assert expected <= written, f"missing: {expected - written}"
    assert not (project / "gitignore").exists() and not (project / "env.example").exists()


def test_dangling_symlink_target_is_kept_not_written_through(
    fake_payload: Path, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    try:
        (project / ".gitignore").symlink_to(outside)
    except OSError as e:
        pytest.skip(f"cannot create symlinks on this host: {e}")

    assert Path(".gitignore") in scaffold_project(project)
    assert not outside.exists()


def test_settings_toml_is_production_shaped_without_api_base_url() -> None:
    data = tomllib.loads((scaffold_source_dir() / "settings.toml").read_text(encoding="utf-8"))
    expected = {
        "root_url": "/my_project",
        "port": 8111,
        "host": "127.0.0.1",
        "debug": True,
        "database_driver": "postgresql+asyncpg",
        "recordflow_enabled": True,
        "pipeline_enabled": True,
        "frontend_enabled": True,
        "config_mode": "python",
        "config_tasks_path": "./plan/",
        "dicom_retrieve_mode": "c-move",
    }
    assert {k: data.get(k) for k in expected} == expected
    assert "api_base_url" not in data


def test_settings_custom_toml_is_inert() -> None:
    assert (
        tomllib.loads((scaffold_source_dir() / "settings.custom.toml").read_text(encoding="utf-8"))
        == {}
    )


def test_env_example_cannot_blank_settings() -> None:
    src = scaffold_source_dir()
    text = (src / "env.example").read_text(encoding="utf-8")
    assert all(line.startswith("#") for line in text.splitlines() if line.strip())
    set_in_toml = set(tomllib.loads((src / "settings.toml").read_text(encoding="utf-8")))
    named = {n.lower() for n in re.findall(r"CLARINET_([A-Z0-9_]+)=", text)}
    assert named, "env.example names no variable"
    assert not named & set_in_toml


@pytest.fixture
def fake_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    src = tmp_path / "payload"
    (src / "plan" / "__pycache__").mkdir(parents=True)
    (src / "plan" / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0")
    (src / "plan" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (src / "gitignore").write_text("data/\n", encoding="utf-8")
    ro = src / "settings.toml"
    ro.write_text("port = 1\n", encoding="utf-8")
    ro.chmod(stat.S_IREAD)  # a read-only install (Review Focus 1)
    monkeypatch.setattr(project_scaffold, "scaffold_source_dir", lambda: src)
    return src


def test_copy_renames_dotfiles_skips_caches_and_creates_parents(
    fake_payload: Path, tmp_path: Path
) -> None:
    project = tmp_path / "a" / "b" / "proj"  # Review Focus 5
    assert scaffold_project(project) == []
    assert (project / ".gitignore").is_file() and not (project / "gitignore").exists()
    assert (project / "plan" / "a.py").is_file()
    assert not list(project.rglob("__pycache__"))


def test_copied_files_are_writable_even_from_a_read_only_payload(
    fake_payload: Path, tmp_path: Path
) -> None:
    scaffold_project(tmp_path / "proj")
    assert os.access(tmp_path / "proj" / "settings.toml", os.W_OK)


def test_rerun_never_overwrites_and_reports_dotted_names(
    fake_payload: Path, tmp_path: Path
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".gitignore").write_text("mine\n", encoding="utf-8")
    (project / "settings.toml").write_text("port = 9\n", encoding="utf-8")

    kept = scaffold_project(project)

    assert sorted(kept) == [Path(".gitignore"), Path("settings.toml")]
    assert (project / ".gitignore").read_text(encoding="utf-8") == "mine\n"
    assert (project / "settings.toml").read_text(encoding="utf-8") == "port = 9\n"


def test_missing_payload_raises_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clarinet, "__file__", str(tmp_path / "pkg" / "__init__.py"))
    project = tmp_path / "proj"
    with pytest.raises(ProjectScaffoldError, match="scaffold payload not found") as exc:
        scaffold_project(project)
    assert "pkg" in str(exc.value) and "scaffold" in str(exc.value)  # names the expected path
    assert not project.exists()
