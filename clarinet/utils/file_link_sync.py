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

    Every link change goes through ``record_type.file_links``: the collection is
    the source of truth, and its ``delete-orphan`` cascade turns a removal into
    the DELETE at the next flush. Never ``session.delete()`` a link that is still
    inside the loaded collection — the stale object's remove events strip the
    freshly added links from it, and the caller reads back ``[]`` (#567;
    mechanism in ``clarinet/repositories/CLAUDE.md``, "M2M Link Lifecycle").

    Args:
        record_type: RecordType to sync links for; ``file_links`` must be loaded.
        file_defs: File definitions to link.
        fd_repo: Repository for upserting FileDefinitions.
        session: Database session.
        clear_existing: If True, delete existing file_links before creating new ones.

    Returns:
        List of newly created ``RecordTypeFileLink`` instances.
    """
    if clear_existing:
        # Flush the orphan DELETEs on their own: a same-PK delete + insert
        # inside one flush is "row-switched" into an UPDATE of the old row,
        # and the point here is a clean DELETE, then a fresh INSERT.
        record_type.file_links = []
        await session.flush()

    if not file_defs:
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
            file_definition_id=file_def.id,
            file_definition=file_def,
            role=role,
            required=fd.required,
            allow_path_collision=fd.allow_path_collision,
        )
        record_type.file_links.append(link)
        new_links.append(link)

    await session.flush()
    return new_links
