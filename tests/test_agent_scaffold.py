"""Unit tests for clarinet.utils.agent_scaffold and the shipped clarinet/docs payload."""

import argparse
import re
from pathlib import Path

import pytest

import clarinet
from clarinet.cli.main import handle_agent_command
from clarinet.exceptions.domain import AgentScaffoldError
from clarinet.utils.agent_scaffold import agent_source_dir, scaffold_agent_docs

MANAGED = Path(".claude") / "rules" / "clarinet"

DOCS = Path(clarinet.__file__).resolve().parent / "docs"
AGENT_CLAUDE = DOCS / "agent" / "claude"

SECTION_RULES = ["definitions", "workflows", "slicer", "schemas", "utils"]
DEEP_DOCS = [
    "recordflow-dsl",
    "slicer-helper-api",
    "pipeline-ops",
    "file-registry",
    "project-setup",
]


def test_payload_files_present() -> None:
    assert (AGENT_CLAUDE / "overview.md").is_file()
    for name in SECTION_RULES:
        assert (AGENT_CLAUDE / f"{name}.md").is_file()
    for name in DEEP_DOCS:
        assert (DOCS / f"{name}.md").is_file()


def test_no_unresolved_clarinet_repo_links() -> None:
    for md in AGENT_CLAUDE.glob("*.md"):
        assert "<clarinet>" not in md.read_text(encoding="utf-8"), md


def test_doc_token_links_resolve() -> None:
    token_link = re.compile(r"\{\{CLARINET_DOCS\}\}/([\w.-]+\.md)")
    for md in AGENT_CLAUDE.glob("*.md"):
        for target in token_link.findall(md.read_text(encoding="utf-8")):
            assert (DOCS / target).is_file(), f"{md} → missing {target}"


def test_agent_source_dir_resolves() -> None:
    src = agent_source_dir("claude")
    assert src.is_dir()
    assert (src / "overview.md").is_file()


def test_agent_source_dir_unknown_agent() -> None:
    with pytest.raises(AgentScaffoldError):
        agent_source_dir("codex")


def test_init_writes_files_header_and_resolved_links(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    assert dest == tmp_path / MANAGED
    overview = (dest / "overview.md").read_text(encoding="utf-8")
    # managed header on first line (overview has no frontmatter)
    assert overview.startswith("<!-- managed by clarinet v")
    # token fully substituted to an existing on-disk docs path
    assert "{{CLARINET_DOCS}}" not in overview
    assert str(DOCS) in overview
    assert (DOCS / "recordflow-dsl.md").is_file()


def test_init_preserves_frontmatter_then_header(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    text = (dest / "definitions.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")  # frontmatter still at the very top
    assert "paths:" in text.split("---\n", 2)[1]
    # header sits AFTER the closing frontmatter delimiter, before the body
    body = text.split("\n---\n", 1)[1]
    assert body.lstrip("\n").startswith("<!-- managed by clarinet v")


def test_init_refuses_existing_without_force(tmp_path: Path) -> None:
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    with pytest.raises(AgentScaffoldError):
        scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    # force overwrites
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init", force=True)


def test_update_requires_existing(tmp_path: Path) -> None:
    with pytest.raises(AgentScaffoldError):
        scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")


def test_update_overwrites_and_reresolves(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    (dest / "overview.md").write_text("STALE", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    refreshed = (dest / "overview.md").read_text(encoding="utf-8")
    assert "STALE" not in refreshed
    assert refreshed.startswith("<!-- managed by clarinet v")


def test_cli_init_then_update(tmp_path: Path) -> None:
    args = argparse.Namespace(
        command="agent", agent_command="init", path=str(tmp_path), agent="claude", force=False
    )
    handle_agent_command(args)
    assert (tmp_path / MANAGED / "overview.md").is_file()

    upd = argparse.Namespace(
        command="agent", agent_command="update", path=str(tmp_path), agent="claude"
    )
    handle_agent_command(upd)  # must not raise now that the dir is populated


def test_cli_init_existing_exits(tmp_path: Path) -> None:
    args = argparse.Namespace(
        command="agent", agent_command="init", path=str(tmp_path), agent="claude", force=False
    )
    handle_agent_command(args)
    with pytest.raises(SystemExit) as exc:
        handle_agent_command(args)
    assert exc.value.code == 1
