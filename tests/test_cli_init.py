"""`clarinet init`: one write-once path from the packaged payload."""

import shutil
import sys
from pathlib import Path

import pytest

import clarinet
from clarinet.cli.main import init_project, main
from clarinet.utils.version import clarinet_version

SKELETON = [
    "settings.toml",
    "settings.custom.toml",
    ".env.example",
    ".gitignore",
    "plan/definitions/record_types.py",
    "plan/workflows/pipeline_flow.py",
    "plan/slicer_hydrators.py",
    "plan/schemas/first-check.schema.json",
    ".claude/CLAUDE.md",
    ".claude/rules/clarinet/definitions.md",
]


def test_init_writes_the_skeleton_and_next_steps(tmp_path: Path, capsys) -> None:
    init_project(str(tmp_path))
    missing = [p for p in SKELETON if not (tmp_path / p).is_file()]
    assert not missing, missing
    assert not (tmp_path / "gitignore").exists() and not (tmp_path / "env.example").exists()
    assert not list(tmp_path.rglob("__pycache__"))
    out = capsys.readouterr().out
    for step in (".env", "clarinet db init", "clarinet run"):
        assert step in out


def test_rerun_keeps_edits_reports_them_and_refreshes_managed_docs(tmp_path: Path, caplog) -> None:
    caplog.set_level("INFO")  # pytest config sets log_level = "WARNING"
    init_project(str(tmp_path))
    (tmp_path / "settings.toml").write_text("project_name = 'mine'\n", encoding="utf-8")
    (tmp_path / ".claude" / "CLAUDE.md").write_text("my study", encoding="utf-8")
    caplog.clear()  # the first run's "Wrote …/settings.toml" must not satisfy the check

    init_project(str(tmp_path))

    assert (tmp_path / "settings.toml").read_text(encoding="utf-8") == "project_name = 'mine'\n"
    assert (tmp_path / ".claude" / "CLAUDE.md").read_text(encoding="utf-8") == "my study"
    kept = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("Kept existing files")
    ]
    assert kept and "settings.toml" in kept[0]
    managed = (tmp_path / ".claude/rules/clarinet/definitions.md").read_text(encoding="utf-8")
    assert f"managed by clarinet v{clarinet_version()}" in managed


def test_missing_payload_exits_1_and_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(clarinet, "__file__", str(tmp_path / "pkg" / "__init__.py"))
    with pytest.raises(SystemExit) as exc:
        init_project(str(tmp_path / "proj"))
    assert exc.value.code == 1
    assert not (tmp_path / "proj").exists()


def test_path_that_is_a_file_exits_1(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    target.write_text("not a dir", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        init_project(str(target))
    assert exc.value.code == 1
    assert target.read_text(encoding="utf-8") == "not a dir"


def test_path_under_a_file_exits_1(tmp_path: Path) -> None:
    blocker = tmp_path / "somefile"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        init_project(str(blocker / "sub"))
    assert exc.value.code == 1
    assert blocker.read_text(encoding="utf-8") == "x"


def test_headerless_agent_doc_survives_init(tmp_path: Path, caplog) -> None:
    mine = tmp_path / ".claude" / "rules" / "clarinet" / "workflows.md"
    mine.parent.mkdir(parents=True)
    mine.write_bytes(b"project-owned\n")

    init_project(str(tmp_path))

    assert mine.read_bytes() == b"project-owned\n"
    assert str(mine) in caplog.text


@pytest.mark.parametrize("flag", [["--template", "research"], ["--list-templates"]])
def test_legacy_flags_are_rejected(tmp_path: Path, monkeypatch, flag) -> None:
    monkeypatch.setattr(sys, "argv", ["clarinet", "init", *flag, str(tmp_path / "p")])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert not (tmp_path / "p").exists()


@pytest.mark.asyncio
async def test_scaffolded_project_settings_and_plan_load(tmp_path: Path, monkeypatch) -> None:
    from clarinet.config.python_loader import load_python_config
    from clarinet.settings import Settings, settings

    init_project(str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CLARINET_DATABASE_HOST", raising=False)
    shutil.copyfile(tmp_path / ".env.example", tmp_path / ".env")  # Review Focus 2

    project = Settings()
    assert project.database_host == Settings.model_fields["database_host"].default
    monkeypatch.setattr(settings, "config_record_types_file", project.config_record_types_file)
    items = await load_python_config(Path(project.config_tasks_path))

    assert sorted(i.name for i in items) == ["example-segment", "first-check"]
