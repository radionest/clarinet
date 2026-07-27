"""Scaffolding for downstream-project agent docs (``clarinet agent init|update``).

Copies framework-authored Claude guidance shipped in the package
(``clarinet/docs/agent/<agent>/``) into a project's ``.claude/rules/<namespace>/``,
substituting the ``{{CLARINET_DOCS}}`` token with the resolved on-disk path of
``clarinet/docs`` so links to the deep reference docs are valid in the running
environment. Pure file/CLI logic — no DB, no app state (mirror of quarto_scaffold).

The payload is split in two. Most documents are *managed*: rewritten on every
run and stamped with a header saying so. The documents in ``SEED_DOCS`` are
*project-owned* — written once to ``<project>/.claude/`` and never rewritten,
because their body asks the user to replace it. Each run also prunes managed
documents the installed version no longer ships, migrating a formerly-managed
seed rather than deleting it.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

import clarinet
from clarinet.exceptions.domain import AgentScaffoldError
from clarinet.utils.logger import logger

# agent name → namespace subdir under <project>/.claude/rules/
KNOWN_AGENTS: dict[str, str] = {"claude": "clarinet"}

_DOCS_TOKEN = "{{CLARINET_DOCS}}"

# Payload docs owned by the project, not the framework: written once to the
# target below (relative to <project>/.claude/), never rewritten by `update`.
# overview.md's own body tells the user to replace its contents, so it cannot
# live in the managed dir where every update would clobber it.
SEED_DOCS: dict[str, str] = {"overview.md": "CLAUDE.md"}

_MANAGED_MARKER = "<!-- managed by clarinet"


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


def _is_managed(text: str) -> bool:
    """True when ``text`` carries the managed header where ``_with_header`` puts it.

    Mirrors the writer: at the top for a plain document, or right after the
    closing frontmatter delimiter for one with ``paths:`` frontmatter. A file
    without it in either place is project-owned and must never be pruned.
    """
    if text.startswith(_MANAGED_MARKER):
        return True
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + len("\n---\n") :].startswith(_MANAGED_MARKER)
    return False


def _without_header(text: str) -> str:
    """Inverse of ``_with_header`` — drop the managed header line, keep the rest."""
    return "\n".join(line for line in text.split("\n") if not line.startswith(_MANAGED_MARKER))


def scaffold_agent_docs(
    agent: str = "claude",
    *,
    project_dir: Path,
    mode: Literal["init", "update"],
    force: bool = False,
) -> Path:
    """Install (``mode="init"``) or refresh (``mode="update"``) the managed agent docs.

    Writes every managed ``*.md`` from the package payload into
    ``project_dir/.claude/rules/<namespace>/``, substituting ``{{CLARINET_DOCS}}``
    with the resolved package docs path and prepending a managed-header comment.
    Then prunes managed docs no longer in the payload and writes any missing
    ``SEED_DOCS`` target under ``project_dir/.claude/`` (never overwriting one
    that exists). Returns the managed dir — the seed lives outside it.

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
        if md.name in SEED_DOCS:
            continue
        text = md.read_text(encoding="utf-8").replace(_DOCS_TOKEN, docs_root.as_posix())
        (dest / md.name).write_text(_with_header(text, header), encoding="utf-8")
        logger.info(f"Wrote {dest / md.name}")

    # Prune before seeding: a managed overview.md the user edited must be
    # migrated to the seed target, and that only happens while the target is
    # still free. Seeding first would occupy it and turn migration into deletion.
    # Runs in every mode — `init --force` over a project scaffolded by an older
    # version reaches the same legacy layout that `update` does, and skipping the
    # migration there would strand the legacy doc for a later `update` to delete.
    _prune_managed_dir(dest, src=src, project_dir=project_dir)

    for payload_name, target_name in SEED_DOCS.items():
        target = project_dir / ".claude" / target_name
        if target.exists():
            logger.info(f"Kept existing {target}")
            continue
        text = (src / payload_name).read_text(encoding="utf-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace(_DOCS_TOKEN, docs_root.as_posix()), encoding="utf-8")
        logger.info(f"Wrote {target}")

    return dest


def _prune_managed_dir(dest: Path, *, src: Path, project_dir: Path) -> None:
    """Drop managed docs the installed version no longer ships.

    Files without the managed header are project-owned and left alone. A doc
    that became a seed (``overview.md``) is *migrated* rather than deleted when
    its target is still free — one-release courtesy for projects scaffolded
    before the split, whose overview may carry the user's own study description.

    A no-op on a freshly created managed dir, so it is safe to call in every mode.
    """
    payload_names = {p.name for p in src.glob("*.md")} - set(SEED_DOCS)
    for stale in sorted(dest.glob("*.md")):
        if stale.name in payload_names:
            continue
        text = stale.read_text(encoding="utf-8")
        if not _is_managed(text):
            continue
        target_name = SEED_DOCS.get(stale.name)
        target = project_dir / ".claude" / target_name if target_name else None
        if target is not None and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_without_header(text), encoding="utf-8")
            logger.info(f"Migrated {stale} to {target}")
        else:
            logger.info(f"Removed stale managed doc {stale}")
        stale.unlink()
