"""
File checksum utilities for hash-based file change detection.

Provides async-compatible SHA256 checksum computation for files
defined in a record type's file registry.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from clarinet.files._fs import run_in_fs_thread

CHUNK_SIZE = 65536


def _sha256(path: Path) -> str:
    """Compute SHA256 of a file synchronously."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


def _sha256_safe(path: Path) -> str | None:
    """Compute SHA256 if the file exists, otherwise return None."""
    if not path.is_file():
        return None
    return _sha256(path)


async def compute_file_checksum(path: Path) -> str | None:
    """Compute SHA256 of a file in the dedicated FS thread pool.

    Args:
        path: Path to the file

    Returns:
        Hex-encoded SHA256 string, or None if file is missing
    """
    return await run_in_fs_thread(_sha256_safe, path)


def checksums_changed(
    old: dict[str, str] | None,
    new: dict[str, str],
) -> set[str]:
    """Return set of changed or new file keys.

    Args:
        old: Previous checksums (None treated as empty)
        new: Current checksums

    Returns:
        Set of file keys that are new or changed
    """
    old = old or {}
    changed: set[str] = set()
    for key, value in new.items():
        if key not in old or old[key] != value:
            changed.add(key)
    return changed
