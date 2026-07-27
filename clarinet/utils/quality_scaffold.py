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


def _clarinet_version() -> str:
    try:
        return version("clarinet")
    except PackageNotFoundError:  # pragma: no cover - source-tree fallback
        return "unknown"


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
        QualityScaffoldError: Missing payload; ``init`` over an existing managed
            config without ``force``; ``update`` with nothing installed.
    """
    src = payload_dir()
    # Read everything before writing anything, so a missing payload file cannot
    # leave a half-written config behind.
    try:
        contents = {
            dest: (src / name).read_text(encoding="utf-8") for name, dest in PAYLOAD.items()
        }
        fragment = (src / FRAGMENT_NAME).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise QualityScaffoldError(f"quality config payload incomplete under {src}: {exc}") from exc

    populated = any((project_dir / dest).is_file() for dest in PAYLOAD.values())
    if mode == "init" and populated and not force:
        raise QualityScaffoldError(
            f"{project_dir} already has managed quality config; run "
            f"'clarinet quality update' (or pass --force)"
        )
    if mode == "update" and not populated:
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
