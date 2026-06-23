"""Scaffolding for downstream-project agent docs (``clarinet agent init|update``).

Copies framework-authored Claude guidance shipped in the package
(``clarinet/docs/agent/<agent>/``) into a project's ``.claude/rules/<namespace>/``,
substituting the ``{{CLARINET_DOCS}}`` token with the resolved on-disk path of
``clarinet/docs`` so links to the deep reference docs are valid in the running
environment. Pure file/CLI logic — no DB, no app state (mirror of quarto_scaffold).
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import clarinet
from clarinet.exceptions.domain import AgentScaffoldError
from clarinet.utils.logger import logger

# agent name → namespace subdir under <project>/.claude/rules/
KNOWN_AGENTS: dict[str, str] = {"claude": "clarinet"}

_DOCS_TOKEN = "{{CLARINET_DOCS}}"


def _clarinet_version() -> str:
    try:
        return version("clarinet")
    except PackageNotFoundError:  # pragma: no cover - source-tree fallback
        return "unknown"


def _package_docs_dir() -> Path:
    """Absolute path of the shipped ``clarinet/docs`` dir (link-target root)."""
    return Path(clarinet.__file__).resolve().parent / "docs"


def agent_source_dir(agent: str) -> Path:
    """Source dir of the delivered set for ``agent`` inside the package.

    Raises:
        AgentScaffoldError: unknown agent, or the payload is missing (e.g. a wheel
            built without ``clarinet/docs``).
    """
    if agent not in KNOWN_AGENTS:
        raise AgentScaffoldError(f"unknown agent {agent!r}: choose from {sorted(KNOWN_AGENTS)}")
    src = _package_docs_dir() / "agent" / agent
    if not src.is_dir():
        raise AgentScaffoldError(f"agent docs payload not found at {src}")
    return src


def _with_header(text: str, header: str) -> str:
    """Insert ``header`` after the YAML frontmatter, or at the top if there is none.

    A leading HTML comment before ``---`` would stop the rules loader recognising
    ``paths:`` frontmatter, so for frontmatter files the header goes right after the
    closing delimiter.
    """
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            insert = end + len("\n---\n")
            return text[:insert] + header + text[insert:]
    return header + text


def scaffold_agent_docs(
    agent: str = "claude",
    *,
    project_dir: Path,
    mode: str,
    force: bool = False,
) -> Path:
    """Install (``mode="init"``) or refresh (``mode="update"``) the managed agent docs.

    Writes every ``*.md`` from the package payload into
    ``project_dir/.claude/rules/<namespace>/``, substituting ``{{CLARINET_DOCS}}``
    with the resolved package docs path and prepending a managed-header comment.
    Returns the managed dir.

    Raises:
        AgentScaffoldError: unknown agent / missing payload; ``init`` over an
            already-populated managed dir without ``force``; ``update`` when the
            managed dir holds no docs.
    """
    src = agent_source_dir(agent)
    dest = project_dir / ".claude" / "rules" / KNOWN_AGENTS[agent]
    populated = dest.is_dir() and any(dest.glob("*.md"))

    if mode == "init" and populated and not force:
        raise AgentScaffoldError(
            f"{dest} already has managed docs; run 'clarinet agent update' (or pass --force)"
        )
    if mode == "update" and not populated:
        raise AgentScaffoldError(f"{dest} has no managed docs; run 'clarinet agent init' first")

    docs_root = _package_docs_dir()
    header = f"<!-- managed by clarinet v{_clarinet_version()} — do not edit; run 'clarinet agent update' -->\n"

    dest.mkdir(parents=True, exist_ok=True)
    for md in sorted(src.glob("*.md")):
        text = md.read_text(encoding="utf-8").replace(_DOCS_TOKEN, str(docs_root))
        (dest / md.name).write_text(_with_header(text, header), encoding="utf-8")
        logger.info(f"Wrote {dest / md.name}")
    return dest
