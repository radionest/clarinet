"""Unit tests for clarinet.utils.agent_scaffold and the shipped clarinet/docs payload."""

import re
from pathlib import Path

import clarinet

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
