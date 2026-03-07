"""Shared utility for synchronizing RecordType ↔ FileDefinition links.

Replaces duplicated inline code in routers, bootstrap, and reconciler
with a single reusable function.
"""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.models.file_schema import FileDefinitionRead, RecordTypeFileLink
from clarinet.models.record import RecordType
from clarinet.repositories.file_definition_repository import FileDefinitionRepository


async def sync_file_links(
    record_type: RecordType,
    file_defs: Sequence[FileDefinitionRead],
    fd_repo: FileDefinitionRepository,
    session: AsyncSession,
    *,
    clear_existing: bool = False,
) -> list[RecordTypeFileLink]:
    """Synchronize file links for a RecordType.

    Upserts ``FileDefinition`` rows and creates ``RecordTypeFileLink`` entries.

    Args:
        record_type: RecordType to sync links for.
        file_defs: File definitions to link.
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

    # Bulk upsert FileDefinitions
    fd_map = await fd_repo.bulk_upsert(file_defs)

    # Create links
    new_links: list[RecordTypeFileLink] = []
    for fd in file_defs:
        file_def = fd_map[fd.name]
        role = fd.role.value if hasattr(fd.role, "value") else str(fd.role)
        link = RecordTypeFileLink(
            record_type_name=record_type.name,
            file_definition_id=file_def.id,  # type: ignore[arg-type]
            role=role,
            required=fd.required,
        )
        session.add(link)
        new_links.append(link)

    await session.flush()
    record_type.file_links = new_links
    return new_links
