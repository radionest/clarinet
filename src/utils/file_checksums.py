"""
File checksum utilities for hash-based file change detection.

Provides async-compatible SHA256 checksum computation for files
tracked by RecordFileAccessor.
"""

import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.file_accessor import RecordFileAccessor

CHUNK_SIZE = 65536


def _sha256(path: Path) -> str:
    """Compute SHA256 of a file synchronously."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK_SIZE):
            h.update(chunk)
    return h.hexdigest()


async def compute_file_checksum(path: Path) -> str | None:
    """Compute SHA256 of a file, async via to_thread.

    Args:
        path: Path to the file

    Returns:
        Hex-encoded SHA256 string, or None if file is missing
    """
    if not path.is_file():
        return None
    return await asyncio.to_thread(_sha256, path)


async def compute_checksums(accessor: RecordFileAccessor) -> dict[str, str]:
    """Compute checksums for all existing files in accessor.

    For singular files, key = file definition name.
    For collections (multiple=True), key = "name:filename".

    Args:
        accessor: RecordFileAccessor with file registry populated

    Returns:
        Dict mapping file key to SHA256 hex string
    """
    checksums: dict[str, str] = {}

    for name in accessor.available():
        fd = accessor._registry[name]
        if fd.multiple:
            paths = accessor._glob(fd)
            for p in paths:
                checksum = await compute_file_checksum(p)
                if checksum is not None:
                    checksums[f"{name}:{p.name}"] = checksum
        else:
            path = accessor._resolve(fd)
            checksum = await compute_file_checksum(path)
            if checksum is not None:
                checksums[name] = checksum

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
