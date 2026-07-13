"""Unit tests for clarinet.utils.agent_scaffold and the shipped clarinet/docs payload."""

import argparse
import re
from pathlib import Path, PureWindowsPath

import pytest

import clarinet
from clarinet.cli.main import handle_agent_command
from clarinet.exceptions.domain import AgentScaffoldError
from clarinet.utils import agent_scaffold
from clarinet.utils.agent_scaffold import agent_source_dir, scaffold_agent_docs

MANAGED = Path(".claude") / "rules" / "clarinet"

DOCS = Path(clarinet.__file__).resolve().parent / "docs"
AGENT_CLAUDE = DOCS / "agent" / "claude"
REPO_ROOT = Path(clarinet.__file__).resolve().parent.parent
RULES_DIR = REPO_ROOT / ".claude" / "rules"
PROJECT_TEMPLATE_RULES = REPO_ROOT / "examples" / "project_template" / ".claude" / "rules"
PROJECT_TEMPLATE_CLAUDE_MD = REPO_ROOT / "examples" / "project_template" / ".claude" / "CLAUDE.md"

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")

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
    assert DOCS.as_posix() in overview
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


@pytest.mark.skipif(not RULES_DIR.is_dir(), reason="repo .claude/rules absent (installed wheel)")
def test_translated_agent_docs_have_no_cyrillic() -> None:
    """Regression guard for the Russian→English translation of agent-facing docs.

    These docs have no byte-identical twin to diff against (unlike DEEP_DOCS), so this
    just asserts no Cyrillic text creeps back in on a future edit.
    """
    files = [
        *AGENT_CLAUDE.glob("*.md"),
        RULES_DIR / "slicer-context.md",
        PROJECT_TEMPLATE_CLAUDE_MD,
        *PROJECT_TEMPLATE_RULES.glob("*.md"),
    ]
    assert len(files) >= 13
    for md in files:
        assert not _CYRILLIC_RE.search(md.read_text(encoding="utf-8")), (
            f"{md} contains Cyrillic text"
        )


@pytest.mark.skipif(not RULES_DIR.is_dir(), reason="repo .claude/rules absent (installed wheel)")
def test_deep_docs_identical_to_rules_seeds() -> None:
    for name in DEEP_DOCS:
        shipped = DOCS / f"{name}.md"
        seed = RULES_DIR / f"{name}.md"
        assert shipped.read_bytes() == seed.read_bytes(), (
            f"clarinet/docs/{name}.md has drifted from .claude/rules/{name}.md — re-copy the seed"
        )


def test_written_deep_doc_links_resolve(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    overview = (dest / "overview.md").read_text(encoding="utf-8")
    deep_link_re = re.compile(r"((?:[A-Za-z]:)?/[^\s`'\"]+/docs/[\w.-]+\.md)")
    matches = deep_link_re.findall(overview)
    deep_matches = [m for m in matches if any(m.endswith(f"{n}.md") for n in DEEP_DOCS)]
    assert deep_matches, "no substituted deep-doc link found in written overview.md"
    for link in deep_matches:
        assert Path(link).is_file(), f"written link does not resolve to a file: {link}"


def test_written_links_use_forward_slashes_for_windows_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regress #419 on POSIX: a backslash docs root must still emit forward-slash links."""
    real_src = agent_source_dir("claude")
    win_docs = PureWindowsPath(r"C:\pkg\clarinet\docs")
    monkeypatch.setattr(agent_scaffold, "agent_source_dir", lambda *_: real_src)
    monkeypatch.setattr(agent_scaffold, "_package_docs_dir", lambda: win_docs)

    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    overview = (dest / "overview.md").read_text(encoding="utf-8")
    assert win_docs.as_posix() in overview  # "C:/pkg/clarinet/docs"
    assert str(win_docs) not in overview  # not the "C:\\pkg\\..." backslash form
