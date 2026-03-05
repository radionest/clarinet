"""Shared utility for synchronizing RecordType ↔ FileDefinition links.

Replaces duplicated inline code in routers, bootstrap, and reconciler
with a single reusable function.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.file_schema import FileDefinitionRead, RecordTypeFileLink
from src.models.record import RecordType
from src.repositories.file_definition_repository import FileDefinitionRepository


def _normalize_fd(fd: FileDefinitionRead | dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
    """Extract FileDefinition data, role, and required flag from either input type.

    Args:
        fd: A ``FileDefinitionRead`` instance or a plain dict.

    Returns:
        Tuple of (fd_data dict for bulk_upsert, role string, required bool).
    """
    if isinstance(fd, FileDefinitionRead):
        fd_data = {
            "name": fd.name,
            "pattern": fd.pattern,
            "description": fd.description,
            "multiple": fd.multiple,
        }
        role = fd.role.value if hasattr(fd.role, "value") else str(fd.role)
        required = fd.required
    else:
        fd_data = {
            "name": fd["name"],
            "pattern": fd["pattern"],
            "description": fd.get("description"),
            "multiple": fd.get("multiple", False),
        }
        role = fd.get("role", "output")
        if hasattr(role, "value"):
            role = role.value
        required = fd.get("required", True)

    return fd_data, role, required


async def sync_file_links(
    record_type: RecordType,
    file_defs: Sequence[FileDefinitionRead | dict[str, Any]],
    fd_repo: FileDefinitionRepository,
    session: AsyncSession,
    *,
    clear_existing: bool = False,
) -> list[RecordTypeFileLink]:
    """Synchronize file links for a RecordType.

    Normalizes input (``FileDefinitionRead`` or plain dicts), upserts
    ``FileDefinition`` rows, and creates ``RecordTypeFileLink`` entries.

    Args:
        record_type: RecordType to sync links for.
        file_defs: List of file definitions (``FileDefinitionRead`` or dicts).
        fd_repo: Repository for upserting FileDefinitions.
        session: Database session.
        clear_existing: If True, delete existing file_links before creating new ones.

    Returns:
        List of newly created ``RecordTypeFileLink`` instances.
    """
    if clear_existing:
        for link in list(record_type.file_links or []):
            await session.delete(link)
        await session.flush()

    if not file_defs:
        record_type.file_links = []
        return []

    # Normalize all entries
    normalized = [_normalize_fd(fd) for fd in file_defs]

    # Bulk upsert FileDefinitions
    fd_data_list = [fd_data for fd_data, _, _ in normalized]
    fd_map = await fd_repo.bulk_upsert(fd_data_list)

    # Create links
    new_links: list[RecordTypeFileLink] = []
    for fd_data, role, required in normalized:
        file_def = fd_map[fd_data["name"]]
        link = RecordTypeFileLink(
            record_type_name=record_type.name,
            file_definition_id=file_def.id,  # type: ignore[arg-type]
            role=role,
            required=required,
        )
        session.add(link)
        new_links.append(link)

    await session.flush()
    record_type.file_links = new_links
    return new_links
