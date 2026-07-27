"""Scaffolding for downstream-project quality config (``clarinet quality init|update``).

Copies the lint/type configuration shipped in the package (``clarinet/quality/``)
into a project root. Structural mirror of :mod:`clarinet.utils.agent_scaffold`:
the payload is resolved *inside* the package so it survives a wheel build.

The pyproject dependency fragment is deliberately RETURNED, never written — a
downstream ``pyproject.toml`` is hand-written and may carry custom indexes,
optional extras and ``[tool.uv]`` settings, and no automatic merge of those is
safe. Pure file/CLI logic — no DB, no app state.
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

import clarinet
from clarinet.exceptions.domain import QualityScaffoldError
from clarinet.utils.logger import logger

# payload filename → destination filename (the ruff config gains its leading dot
# here; shipping a dotfile inside the package is fragile across build backends).
PAYLOAD: dict[str, str] = {
    "mypy.ini": "mypy.ini",
    "ruff.toml": ".ruff.toml",
    "Makefile": "Makefile",
}

FRAGMENT_NAME = "pyproject.fragment.toml"

# Comment syntax per destination — a '#'-prefixed header is valid in ini, toml
# and make alike, so one form covers every payload file.
_HEADER_PREFIX = "#"

# Prefix-only (no version number): a file managed by an older clarinet must
# still be recognised as managed so `update` can refresh it.
_MANAGED_MARKER = f"{_HEADER_PREFIX} managed by clarinet"


def _clarinet_version() -> str:
    try:
        return version("clarinet")
    except PackageNotFoundError:  # pragma: no cover - source-tree fallback
        return "unknown"


def _is_managed(path: Path) -> bool:
    """True if ``path`` exists and its first line carries clarinet's managed marker."""
    if not path.is_file():
        return False
    with path.open(encoding="utf-8") as f:
        return f.readline().startswith(_MANAGED_MARKER)


def payload_dir() -> Path:
    """Absolute path of the shipped ``clarinet/quality`` payload dir.

    Raises:
        QualityScaffoldError: The payload is missing (e.g. a wheel built without
            the ``clarinet/quality/**/*`` artifacts entry).
    """
    src = Path(clarinet.__file__).resolve().parent / "quality"
    if not src.is_dir():
        raise QualityScaffoldError(f"quality config payload not found at {src}")
    return src


def scaffold_quality_config(
    *,
    project_dir: Path,
    mode: Literal["init", "update"],
    force: bool = False,
) -> tuple[Path, str]:
    """Install (``mode="init"``) or refresh (``mode="update"``) the quality config.

    Returns ``(project_dir, fragment_text)``. The caller is responsible for
    printing the fragment; this function never writes it and never touches
    ``pyproject.toml``.

    Raises:
        QualityScaffoldError: Missing or incomplete payload; ``init`` over an
            existing managed config without ``force``; ``init`` colliding with
            an unmanaged file at one of the destination names without
            ``force``; ``update`` with nothing managed yet.
    """
    src = payload_dir()
    # Read everything before writing anything, so a missing payload file cannot
    # leave a half-written config behind.
    try:
        contents = {
            dest: (src / name).read_text(encoding="utf-8") for name, dest in PAYLOAD.items()
        }
        fragment = (src / FRAGMENT_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise QualityScaffoldError(f"quality config payload incomplete under {src}: {exc}") from exc

    # "Managed" means clarinet wrote it (carries the header), not just "a file
    # happens to exist at this name" -- a downstream project's own hand-written
    # Makefile must never be mistaken for a stale clarinet config (see the
    # review that added this check: bare existence let `init` misdirect an
    # operator into `update`, which then clobbered it with no `--force`).
    managed = any(_is_managed(project_dir / dest) for dest in PAYLOAD.values())
    unmanaged = [
        dest
        for dest in PAYLOAD.values()
        if (project_dir / dest).is_file() and not _is_managed(project_dir / dest)
    ]

    if mode == "init" and managed and not force:
        raise QualityScaffoldError(
            f"{project_dir} already has managed quality config; run "
            f"'clarinet quality update' (or pass --force)"
        )
    if mode == "init" and unmanaged and not force:
        listed = ", ".join(unmanaged)
        raise QualityScaffoldError(
            f"{project_dir} has {listed} not written by clarinet; move it aside or pass --force"
        )
    if mode == "update" and not managed:
        raise QualityScaffoldError(
            f"{project_dir} has no managed quality config; run 'clarinet quality init' first"
        )

    header = (
        f"{_HEADER_PREFIX} managed by clarinet v{_clarinet_version()} — "
        f"run 'clarinet quality update' to refresh\n"
    )

    project_dir.mkdir(parents=True, exist_ok=True)
    for dest, text in contents.items():
        (project_dir / dest).write_text(header + text, encoding="utf-8")
        logger.info(f"Wrote {project_dir / dest}")
    return project_dir, fragment
