"""Scaffolding for downstream-project agent docs (``clarinet agent init|update``).

Copies framework-authored Claude guidance shipped in the package
(``clarinet/docs/agent/<agent>/``) into a project's ``.claude/rules/<namespace>/``,
substituting the ``{{CLARINET_DOCS}}`` token with the resolved on-disk path of
``clarinet/docs`` so links to the deep reference docs are valid in the running
environment. Pure file/CLI logic — no DB, no app state (mirror of quarto_scaffold).

Most payload docs are *managed* (``utils.managed_files``): rewritten on every
run and stamped with the header. ``SEED_DOCS`` are project-owned — written once
under ``<project>/.claude/`` and never rewritten, because their body asks the
user to replace it. Each run also prunes managed docs the installed version no
longer ships, moving a formerly managed seed to its seed path instead.
"""

from pathlib import Path
from typing import Literal

import clarinet
from clarinet.exceptions.domain import AgentScaffoldError
from clarinet.utils.logger import logger
from clarinet.utils.managed_files import is_managed, managed_header, strip_header, with_header

# agent name → namespace subdir under <project>/.claude/rules/
KNOWN_AGENTS: dict[str, str] = {"claude": "clarinet"}

# payload doc → seed path under <project>/.claude/
SEED_DOCS: dict[str, str] = {"overview.md": "CLAUDE.md"}

_DOCS_TOKEN = "{{CLARINET_DOCS}}"


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


def scaffold_agent_docs(
    agent: str = "claude",
    *,
    project_dir: Path,
    mode: Literal["init", "update"],
    force: bool = False,
) -> Path:
    """Install (``init``) or refresh (``update``) the agent docs; return the managed dir.

    Writes every payload ``*.md`` except ``SEED_DOCS`` into
    ``project_dir/.claude/rules/<namespace>/`` with ``{{CLARINET_DOCS}}``
    resolved and the managed header added, prunes managed docs the payload no
    longer ships, then writes each seed whose target is absent. An existing
    file without the managed header is project-owned: kept, with a warning.

    Raises:
        AgentScaffoldError: unknown agent / missing payload; ``init`` when the
            managed dir already holds a managed doc and ``force`` is off;
            ``update`` when it holds none.
    """
    src = agent_source_dir(agent)
    dest = project_dir / ".claude" / "rules" / KNOWN_AGENTS[agent]
    has_managed = any(is_managed(p) for p in dest.glob("*.md"))

    if mode == "init" and has_managed and not force:
        raise AgentScaffoldError(
            f"{dest} already has managed docs; run 'clarinet agent update' (or pass --force)"
        )
    if mode == "update" and not has_managed:
        raise AgentScaffoldError(f"{dest} has no managed docs; run 'clarinet agent init' first")

    docs_root = _package_docs_dir().as_posix()
    header = managed_header("<!--", "clarinet agent update")
    dest.mkdir(parents=True, exist_ok=True)
    for md in sorted(src.glob("*.md")):
        if md.name in SEED_DOCS:
            continue
        target = dest / md.name
        if target.exists() and not is_managed(target):
            logger.warning(
                f"Kept {target}: it has no managed header, so it is project-owned. "
                f"Delete it to receive clarinet's {md.name}"
            )
            continue
        text = md.read_text(encoding="utf-8").replace(_DOCS_TOKEN, docs_root)
        target.write_text(with_header(text, header), encoding="utf-8")
        logger.info(f"Wrote {target}")

    # Prune before seeding: a legacy managed overview.md can move to the seed
    # path only while that path is still free.
    _prune(dest, src=src, project_dir=project_dir)

    for name, seed_name in SEED_DOCS.items():
        seed = project_dir / ".claude" / seed_name
        if seed.exists():
            continue
        text = (src / name).read_text(encoding="utf-8").replace(_DOCS_TOKEN, docs_root)
        seed.write_text(text, encoding="utf-8")
        logger.info(f"Wrote {seed}")
    return dest


def _prune(dest: Path, *, src: Path, project_dir: Path) -> None:
    """Remove managed docs the payload no longer ships; move a legacy managed seed.

    Files without the managed header are never touched. A legacy seed moves
    (header stripped) only when its seed path is free; otherwise it stays and a
    warning names both paths, so no user edit is lost.
    """
    shipped = {p.name for p in src.glob("*.md")} - set(SEED_DOCS)
    for stale in sorted(dest.glob("*.md")):
        if stale.name in shipped or not is_managed(stale):
            continue
        seed_name = SEED_DOCS.get(stale.name)
        if seed_name is None:
            stale.unlink()
            logger.info(f"Removed {stale}: no longer shipped by clarinet")
            continue
        seed = project_dir / ".claude" / seed_name
        if seed.exists():
            logger.warning(
                f"Kept {stale}: {seed} already exists. Move anything you need from "
                f"{stale} into {seed}, then delete {stale}"
            )
            continue
        seed.write_text(strip_header(stale.read_text(encoding="utf-8")), encoding="utf-8")
        stale.unlink()
        logger.info(f"Moved {stale} to {seed}")
