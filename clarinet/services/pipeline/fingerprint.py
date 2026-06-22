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

# Non-source artifacts that differ between deploys without meaning a code change.
_SKIP_DIR_PARTS = {"__pycache__", ".git"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".log", ".swp", ".tmp"}
_SKIP_NAMES = {".DS_Store"}


def clarinet_version() -> str:
    """Installed clarinet package version, or ``"unknown"`` if not installed."""
    try:
        return version("clarinet")
    except PackageNotFoundError:
        return "unknown"


def compute_plan_hash(root: Path) -> str:
    """Deterministic sha256 over all source files under *root*.

    Files are sorted by relative POSIX path; both the path and the content feed
    the hash (so renames change the result). Non-source artifacts are skipped.
    A missing root hashes as empty.
    """
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and not _SKIP_DIR_PARTS & set(p.parts)
        and p.suffix not in _SKIP_SUFFIXES
        and p.name not in _SKIP_NAMES
    )
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


@lru_cache(maxsize=1)
def compute_fingerprint() -> str:
    """Full version fingerprint (startup snapshot, cached for process life)."""
    plan_hash = compute_plan_hash(Path(settings.config_tasks_path))
    return f"{clarinet_version()}:{plan_hash}"


@lru_cache(maxsize=1)
def queue_version_segment() -> str:
    """Short, queue-name-safe segment derived from the full fingerprint."""
    return hashlib.sha256(compute_fingerprint().encode()).hexdigest()[:12]


def reset_fingerprint_cache() -> None:
    """Clear cached fingerprint/segment — for tests that mutate config_tasks_path."""
    compute_fingerprint.cache_clear()
    queue_version_segment.cache_clear()
