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
# overview.md is delivered as a project-owned seed, not a managed rule
SEED = Path(".claude") / "CLAUDE.md"

DOCS = Path(clarinet.__file__).resolve().parent / "docs"
AGENT_CLAUDE = DOCS / "agent" / "claude"
REPO_ROOT = Path(clarinet.__file__).resolve().parent.parent
RULES_DIR = REPO_ROOT / ".claude" / "rules"

_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")

SECTION_RULES = [
    "definitions",
    "workflows",
    "anonymization",
    "slicer",
    "schemas",
    "utils",
    "scripting",
]
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
    # the seed carries the token substitution but no managed header
    overview = (tmp_path / SEED).read_text(encoding="utf-8")
    assert not overview.startswith("<!-- managed by clarinet v")
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
    (dest / "definitions.md").write_text("STALE", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    refreshed = (dest / "definitions.md").read_text(encoding="utf-8")
    assert "STALE" not in refreshed
    assert "<!-- managed by clarinet v" in refreshed


def test_cli_init_then_update(tmp_path: Path) -> None:
    args = argparse.Namespace(
        command="agent", agent_command="init", path=str(tmp_path), agent="claude", force=False
    )
    handle_agent_command(args)
    assert (tmp_path / MANAGED / "definitions.md").is_file()
    assert (tmp_path / SEED).is_file()

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
    ]
    assert len(files) >= 9
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
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    overview = (tmp_path / SEED).read_text(encoding="utf-8")
    deep_link_re = re.compile(r"((?:[A-Za-z]:)?/[^\s`'\"]+/docs/[\w.-]+\.md)")
    matches = deep_link_re.findall(overview)
    deep_matches = [m for m in matches if any(m.endswith(f"{n}.md") for n in DEEP_DOCS)]
    assert deep_matches, "no substituted deep-doc link found in written overview.md"
    for link in deep_matches:
        assert Path(link).is_file(), f"written link does not resolve to a file: {link}"


SEED_DOC = "overview"

_LEGACY_HEADER = (
    "<!-- managed by clarinet v0.10.20 — do not edit; run 'clarinet agent update' -->\n"
)


def test_seed_written_to_project_claude_md(tmp_path: Path) -> None:
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    seed = tmp_path / SEED
    assert seed.is_file()
    assert "managed by clarinet" not in seed.read_text(encoding="utf-8")
    assert not (tmp_path / MANAGED / f"{SEED_DOC}.md").exists()


def test_seed_not_overwritten_on_init(tmp_path: Path) -> None:
    seed = tmp_path / SEED
    seed.parent.mkdir(parents=True)
    seed.write_text("my study", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    assert seed.read_text(encoding="utf-8") == "my study"


def test_seed_survives_update(tmp_path: Path) -> None:
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    seed = tmp_path / SEED
    seed.write_text("my edited study", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert seed.read_text(encoding="utf-8") == "my edited study"


def test_update_prunes_dropped_managed_doc(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    stale = dest / "retired.md"
    stale.write_text(f"{_LEGACY_HEADER}old\n", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert not stale.exists()


def test_update_keeps_unmanaged_file(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    mine = dest / "mine.md"
    mine.write_text("hand-written\n", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert mine.read_text(encoding="utf-8") == "hand-written\n"


def test_update_migrates_legacy_managed_overview(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    legacy = dest / "overview.md"
    legacy.write_text(
        f"{_LEGACY_HEADER}# My Study\nedited by the user\n",
        encoding="utf-8",
    )
    (tmp_path / SEED).unlink()
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    seed_text = (tmp_path / SEED).read_text(encoding="utf-8")
    assert not legacy.exists()
    assert "edited by the user" in seed_text
    assert "managed by clarinet" not in seed_text


def test_forced_init_migrates_legacy_overview_instead_of_stranding_it(tmp_path: Path) -> None:
    """`init --force` reaches the same legacy layout `update` does.

    If it wrote the seed without pruning, the legacy managed overview.md would
    survive with the user's edits and the *next* update would find the seed
    target occupied and delete it.
    """
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    (dest / "overview.md").write_text(
        f"{_LEGACY_HEADER}# My Study\nedited by the user\n", encoding="utf-8"
    )
    (tmp_path / SEED).unlink()

    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init", force=True)

    assert not (dest / "overview.md").exists()
    seed_text = (tmp_path / SEED).read_text(encoding="utf-8")
    assert "edited by the user" in seed_text
    assert "managed by clarinet" not in seed_text


def test_migration_does_not_clobber_existing_seed(tmp_path: Path) -> None:
    dest = scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    (dest / "overview.md").write_text(f"{_LEGACY_HEADER}legacy\n", encoding="utf-8")
    seed = tmp_path / SEED
    seed.write_text("mine", encoding="utf-8")
    scaffold_agent_docs("claude", project_dir=tmp_path, mode="update")
    assert seed.read_text(encoding="utf-8") == "mine"
    assert not (dest / "overview.md").exists()


def test_written_links_use_forward_slashes_for_windows_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regress #419 on POSIX: a backslash docs root must still emit forward-slash links."""
    real_src = agent_source_dir("claude")
    win_docs = PureWindowsPath(r"C:\pkg\clarinet\docs")
    monkeypatch.setattr(agent_scaffold, "agent_source_dir", lambda *_: real_src)
    monkeypatch.setattr(agent_scaffold, "_package_docs_dir", lambda: win_docs)

    scaffold_agent_docs("claude", project_dir=tmp_path, mode="init")
    overview = (tmp_path / SEED).read_text(encoding="utf-8")
    assert win_docs.as_posix() in overview  # "C:/pkg/clarinet/docs"
    assert str(win_docs) not in overview  # not the "C:\\pkg\\..." backslash form
