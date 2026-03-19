"""
File checksum utilities for hash-based file change detection.

Provides async-compatible SHA256 checksum computation for files
defined in a record type's file registry.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.utils.fs import run_in_fs_thread

if TYPE_CHECKING:
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.record import RecordBase

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


async def compute_checksums(
    file_defs: list[FileDefinitionRead],
    record: RecordBase,
    working_dir: Path,
) -> dict[str, str]:
    """Compute checksums for all existing files in file definitions.

    For singular files, key = file definition name.
    For collections (multiple=True), key = "name:filename".

    Args:
        file_defs: List of file definitions to compute checksums for
        record: Record for pattern placeholder resolution
        working_dir: Base directory where files are located

    Returns:
        Dict mapping file key to SHA256 hex string
    """
    from clarinet.utils.file_patterns import glob_file_paths, resolve_pattern

    checksums: dict[str, str] = {}
    for fd in file_defs:
        if fd.multiple:
            paths = await run_in_fs_thread(glob_file_paths, fd, working_dir)
            for p in paths:
                checksum = await compute_file_checksum(p)
                if checksum is not None:
                    checksums[f"{fd.name}:{p.name}"] = checksum
        else:
            path = working_dir / resolve_pattern(fd.pattern, record)
            checksum = await compute_file_checksum(path)
            if checksum is not None:
                checksums[fd.name] = checksum
    return checksums


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
