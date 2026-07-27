"""Version fingerprint for pipeline worker/API compatibility gating.

The fingerprint pins the clarinet package version plus a content hash of the
downstream ``plan/`` directory (``settings.config_tasks_path``). It is a startup
snapshot (``lru_cache``) — it reflects the code loaded into memory, not the files
currently on disk, so a ``git pull`` without a restart cannot fake a match.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from clarinet.settings import settings

# Non-source artifacts that differ between deploys/hosts without meaning a code
# change — host-local config, editor/build scratch, VCS internals.
_SKIP_DIR_PARTS = {
    "__pycache__",
    ".git",
    ".idea",
    ".vscode",
    ".ipynb_checkpoints",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".swp", ".tmp"}
_SKIP_NAMES = {".DS_Store", ".env"}


def _is_source_file(p: Path) -> bool:
    """True when *p* is a file that should feed the plan hash."""
    return (
        p.is_file()
        and not _SKIP_DIR_PARTS & set(p.parts)
        and p.suffix not in _SKIP_SUFFIXES
        and p.name not in _SKIP_NAMES
        and not p.name.endswith("~")  # editor backups
    )


def clarinet_version() -> str:
    """Installed clarinet package version, or ``"unknown"`` if not installed."""
    try:
        return version("clarinet")
    except PackageNotFoundError:
        return "unknown"


def compute_plan_hash(root: Path) -> str:
    """Deterministic, OS-independent sha256 over all source files under *root*.

    Files are sorted by their relative POSIX path (stable across operating
    systems, unlike sorting ``Path`` objects); both the path and the content
    feed the hash, so renames change the result. Line endings are normalized to
    ``\\n`` before hashing so a CRLF/LF checkout difference (git ``autocrlf``)
    does not flip the hash between a Linux API and a Windows worker. Non-source
    artifacts are skipped. A missing root hashes as empty.
    """
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    files = sorted(
        (p for p in root.rglob("*") if _is_source_file(p)),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    for p in files:
        rel = p.relative_to(root).as_posix()
        content = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        h.update(rel.encode())
        h.update(b"\0")
        h.update(content)
        h.update(b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def compute_fingerprint() -> str:
    """Full version fingerprint (startup snapshot, cached for process life).

    ``config_tasks_path`` is resolved to an absolute path; a relative default
    (``./tasks/``) therefore resolves against the process CWD, so the API and
    workers must start from the same project root to agree on the hash.
    """
    plan_hash = compute_plan_hash(Path(settings.config_tasks_path).resolve())
    return f"{clarinet_version()}:{plan_hash}"


@lru_cache(maxsize=1)
def queue_version_segment() -> str:
    """Short, queue-name-safe segment derived from the full fingerprint."""
    return hashlib.sha256(compute_fingerprint().encode()).hexdigest()[:12]


def reset_fingerprint_cache() -> None:
    """Clear cached fingerprint/segment — for tests that mutate config_tasks_path."""
    compute_fingerprint.cache_clear()
    queue_version_segment.cache_clear()
