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

    Links are removed and added through ``record_type.file_links`` only — never
    via ``session.delete()`` / ``session.add()`` on the link. The relationship
    carries ``delete-orphan``, so the collection is the source of truth: a link
    dropped from it is deleted at the next flush, one appended is inserted with
    its parent set. Deleting the rows directly left the deleted links inside
    the loaded collection; assigning the new list then fired a remove event per
    stale link, whose backref cascade ran ``list.remove()`` against the *new*
    collection — and pydantic's value-based ``__eq__`` on ``RecordTypeFileLink``
    matched the freshly inserted link with the same columns, dropping it. The
    DB was right while ``record_type.file_links`` (hence every response served
    from the identity map) came back empty (#567).

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
        # Orphaned links are deleted by this flush; the later INSERT of a link
        # with the same (record_type_name, file_definition_id) needs them gone.
        record_type.file_links = []
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
            file_definition_id=file_def.id,
            role=role,
            required=fd.required,
            allow_path_collision=fd.allow_path_collision,
        )
        record_type.file_links.append(link)
        new_links.append(link)

    await session.flush()
    return new_links
