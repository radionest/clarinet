"""Unit tests for the packaged project scaffold payload."""

from pathlib import Path

import clarinet
from clarinet.utils.project_scaffold import SCAFFOLD_DOTFILES, scaffold_source_dir

PACKAGE_ROOT = Path(clarinet.__file__).resolve().parent

# Every file clarinet init writes, as a payload-relative path.
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


def test_scaffold_root_is_inside_the_package() -> None:
    """Regression guard for #472: the payload must ship with the wheel."""
    src = scaffold_source_dir()
    assert src.is_dir()
    assert PACKAGE_ROOT in src.parents or src.parent == PACKAGE_ROOT


def test_payload_manifest_complete() -> None:
    src = scaffold_source_dir()
    missing = [rel for rel in EXPECTED_PAYLOAD if not (src / rel).is_file()]
    assert not missing, f"payload files missing from the package: {missing}"


def test_payload_carries_no_dotfiles() -> None:
    """A .gitignore inside the package would govern what git — and the wheel — keeps."""
    assert not list(scaffold_source_dir().rglob(".gitignore"))


def test_payload_carries_no_agent_docs() -> None:
    assert not (scaffold_source_dir() / ".claude").exists()


def test_dotfile_map_targets_are_dotted() -> None:
    assert SCAFFOLD_DOTFILES == {"gitignore": ".gitignore", "env.example": ".env.example"}
