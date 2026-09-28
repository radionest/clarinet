"""The packaged project scaffold behind ``clarinet init``.

The payload lives inside the package so it ships in every wheel (#472),
resolved off ``clarinet.__file__`` like the agent and quality payloads. Files
whose target name starts with a dot are stored undotted: a ``.gitignore``
inside the package would govern what git — and so the build — ships.
"""

import shutil
from pathlib import Path

import clarinet
from clarinet.exceptions.domain import ProjectScaffoldError
from clarinet.utils.logger import logger

# payload name → name written into the project
SCAFFOLD_DOTFILES: dict[str, str] = {"gitignore": ".gitignore", "env.example": ".env.example"}


def scaffold_source_dir() -> Path:
    """Absolute path of the shipped payload.

    Raises:
        ProjectScaffoldError: the payload directory is missing.
    """
    src = Path(clarinet.__file__).resolve().parent / "scaffold"
    if not src.is_dir():
        raise ProjectScaffoldError(f"project scaffold payload not found at {src}")
    return src


def scaffold_project(project_dir: Path) -> list[Path]:
    """Copy every payload file whose target is missing; return the kept ones.

    Write-once: an existing file is never overwritten, so re-running is safe.
    ``copyfile`` (not ``copy2``) so a read-only install still yields editable
    files. Byte-code caches are skipped. Kept paths are project-relative, under
    their written (dotted) names.

    Raises:
        ProjectScaffoldError: the payload is missing, or *project_dir* is an
            existing non-directory — before anything is written.
    """
    src = scaffold_source_dir()
    if project_dir.exists() and not project_dir.is_dir():
        raise ProjectScaffoldError(f"{project_dir} exists and is not a directory")
    kept: list[Path] = []
    for item in sorted(src.rglob("*")):
        if item.is_dir() or "__pycache__" in item.parts:
            continue
        rel = item.relative_to(src).as_posix()
        target_rel = Path(SCAFFOLD_DOTFILES.get(rel, rel))
        target = project_dir / target_rel
        if target.exists() or target.is_symlink():  # a dangling link is kept, not written through
            kept.append(target_rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        logger.info(f"Wrote {target}")
    return kept
