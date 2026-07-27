"""Unit tests for `clarinet init`."""

from pathlib import Path

import pytest

from clarinet.cli.main import init_project


def test_init_produces_plan_layout(tmp_path: Path) -> None:
    init_project(str(tmp_path))
    assert (tmp_path / "plan" / "definitions" / "record_types.py").is_file()
    assert (tmp_path / "plan" / "workflows" / "pipeline_flow.py").is_file()
    assert not (tmp_path / "tasks").exists()


def test_init_materializes_dotfiles(tmp_path: Path) -> None:
    init_project(str(tmp_path))
    assert (tmp_path / ".gitignore").is_file()
    assert (tmp_path / ".env.example").is_file()
    assert not (tmp_path / "gitignore").exists()
    assert not (tmp_path / "env.example").exists()


def test_init_installs_agent_docs(tmp_path: Path) -> None:
    init_project(str(tmp_path))
    assert (tmp_path / ".claude" / "CLAUDE.md").is_file()
    assert (tmp_path / ".claude" / "rules" / "clarinet" / "definitions.md").is_file()


def test_init_never_overwrites(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text("project_name = 'mine'\n", encoding="utf-8")
    init_project(str(tmp_path))
    assert settings_file.read_text(encoding="utf-8") == "project_name = 'mine'\n"
    assert (tmp_path / "plan").is_dir()


def test_init_signature_has_no_template_param() -> None:
    import inspect

    assert list(inspect.signature(init_project).parameters) == ["path"]


def test_templates_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("clarinet.cli.templates")


def test_init_is_rerunnable(tmp_path: Path) -> None:
    """A second init must not abort on the managed docs the first one wrote.

    The payload loop writes before agent docs are installed, so an exception
    there leaves a half-scaffolded directory behind.
    """
    init_project(str(tmp_path))
    init_project(str(tmp_path))

    assert (tmp_path / ".claude" / "rules" / "clarinet" / "definitions.md").is_file()
    assert (tmp_path / "plan" / "definitions" / "record_types.py").is_file()


def test_rerun_keeps_the_edited_seed(tmp_path: Path) -> None:
    init_project(str(tmp_path))
    seed = tmp_path / ".claude" / "CLAUDE.md"
    seed.write_text("my study", encoding="utf-8")

    init_project(str(tmp_path))

    assert seed.read_text(encoding="utf-8") == "my study"
