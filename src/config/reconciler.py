"""Reconcile RecordType definitions from config files with the database.

Compares a list of ``RecordTypeCreate`` objects against existing DB rows,
creating new RecordTypes, updating changed ones, and optionally deleting
orphans no longer in the config.

File definitions are normalized into the ``filedefinition`` table and
bound to RecordTypes via ``recordtype_file_link`` (M2M).
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic_core import PydanticUndefined
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from src.exceptions.domain import ValidationError
from src.models.file_schema import FileDefinitionRead, RecordTypeFileLink
from src.models.record import RecordType, RecordTypeCreate
from src.repositories.file_definition_repository import FileDefinitionRepository
from src.utils.file_link_sync import sync_file_links
from src.utils.graph_validation import detect_cycle
from src.utils.logger import logger

# Fields compared when detecting changes between config and DB.
# file_registry is handled separately via link diff.
_COMPARED_FIELDS: tuple[str, ...] = (
    "description",
    "label",
    "level",
    "parent_type_name",
    "role_name",
    "min_records",
    "max_records",
    "slicer_script",
    "slicer_script_args",
    "slicer_result_validator",
    "slicer_result_validator_args",
    "data_schema",
)


@dataclass
class ReconcileResult:
    """Outcome of a reconcile operation.

    Attributes:
        created: Names of newly created RecordTypes.
        updated: Names of RecordTypes that were updated.
        unchanged: Names of RecordTypes that matched exactly.
        orphaned: Names of DB RecordTypes not in config.
        errors: List of (name, message) for items that failed.
    """

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    orphaned: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)


def _normalize(value: Any) -> Any:
    """Normalize a value for comparison.

    Enums are converted to their string value, empty lists/dicts become None.
    """
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (list,dict)) and not value:
        return None
    return value


def _get_field_default(field_name: str) -> Any:
    """Get the normalized default value for a RecordType field.

    Uses Pydantic v2 model introspection to extract the default from the
    ORM model class.
    """
    field_info = RecordType.model_fields.get(field_name)
    if field_info is None:
        return PydanticUndefined

    if field_info.default is not PydanticUndefined:
        return _normalize(field_info.default)
    if field_info.default_factory is not None:
        return _normalize(field_info.default_factory())  # type: ignore[call-arg]

    return PydanticUndefined


def _fields_differ(db_record_type: RecordType, config: RecordTypeCreate) -> list[str]:
    """Return list of scalar field names that differ between DB and config.

    Does NOT compare file_registry — that is handled separately via link diff.
    Only compares fields explicitly set in the config (via ``model_fields_set``).

    Args:
        db_record_type: Existing RecordType from DB.
        config: Typed config object.

    Returns:
        List of field names that have different values.
    """
    changed: list[str] = []
    for field_name in _COMPARED_FIELDS:
        if field_name not in config.model_fields_set:
            continue
        db_val = _normalize(getattr(db_record_type, field_name, None))
        config_val = _normalize(getattr(config, field_name, None))
        if db_val != config_val:
            if config_val is None and db_val == _get_field_default(field_name):
                continue
            changed.append(field_name)
    return changed


def _file_links_differ(
    existing_links: list[RecordTypeFileLink],
    config_defs: list[FileDefinitionRead],
) -> bool:
    """Check whether the file link set differs from config definitions."""
    if len(existing_links) != len(config_defs):
        return True

    # Build comparable sets (name, role, required, level)
    existing_set: set[tuple[str, str, bool, str | None]] = set()
    for link in existing_links:
        level = link.file_definition.level.value if link.file_definition.level else None
        existing_set.add((link.file_definition.name, link.role.value, link.required, level))

    config_set: set[tuple[str, str, bool, str | None]] = set()
    for fd in config_defs:
        level = fd.level.value if fd.level else None
        config_set.add((fd.name, fd.role.value, fd.required, level))

    return existing_set != config_set


async def reconcile_record_types(
    config_items: list[RecordTypeCreate],
    session: AsyncSession,
    *,
    delete_orphans: bool = False,
) -> ReconcileResult:
    """Diff config definitions against DB and apply changes.

    Algorithm:
    1. Load all existing RecordTypes from DB with file_links.
    2. For each config entry: CREATE if new, UPDATE if changed, skip if identical.
    3. DB names not in config -> orphans (warn or delete).
    4. Single commit at the end.

    Args:
        config_items: List of typed RecordTypeCreate objects.
        session: Async database session.
        delete_orphans: If True, delete DB RecordTypes not in config.

    Returns:
        ReconcileResult with counts per category.
    """
    result = ReconcileResult()
    fd_repo = FileDefinitionRepository(session)

    # 1. Load existing RecordTypes with file_links eagerly loaded
    stmt = select(RecordType).options(
        selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition)  # type: ignore[arg-type]  # type: ignore[arg-type]
    )
    db_result = await session.execute(stmt)
    db_types: dict[str, RecordType] = {rt.name: rt for rt in db_result.scalars().all()}

    config_names: set[str] = set()

    # 2. Process each config entry
    for config_item in config_items:
        name = config_item.name
        config_names.add(name)

        try:
            # Extract file defs: None if not explicitly set, empty list if set to empty
            config_defs: list[FileDefinitionRead] | None = None
            if "file_registry" in config_item.model_fields_set:
                config_defs = config_item.file_registry or []

            if name not in db_types:
                # CREATE — new RecordType
                create_data = config_item.model_dump(exclude={"file_registry"})
                new_rt = RecordType.model_validate(create_data)
                new_rt.file_links = []  # Initialize empty, will be populated below
                session.add(new_rt)
                await session.flush()

                # Sync file links
                if config_defs:
                    await sync_file_links(new_rt, config_defs, fd_repo, session)

                result.created.append(name)
                logger.info(f"Config reconcile: created '{name}'")
            else:
                # DIFF — check scalar fields and file links
                existing = db_types[name]
                changed_fields = _fields_differ(existing, config_item)

                files_changed = False
                if config_defs is not None:
                    files_changed = _file_links_differ(existing.file_links or [], config_defs)

                if changed_fields or files_changed:
                    # Update scalar fields
                    for field_name in changed_fields:
                        setattr(existing, field_name, getattr(config_item, field_name))

                    # Sync file links if they changed
                    if files_changed and config_defs is not None:
                        await sync_file_links(
                            existing, config_defs, fd_repo, session, clear_existing=True
                        )

                    all_changed = list(changed_fields)
                    if files_changed:
                        all_changed.append("file_registry")
                    result.updated.append(name)
                    logger.info(
                        f"Config reconcile: updated '{name}' (fields: {', '.join(all_changed)})"
                    )
                else:
                    result.unchanged.append(name)
        except Exception as e:
            result.errors.append((name, str(e)))
            logger.error(f"Config reconcile error for '{name}': {e}")

    # 3. Detect orphans
    orphan_names = set(db_types.keys()) - config_names
    for orphan_name in sorted(orphan_names):
        if delete_orphans:
            await session.delete(db_types[orphan_name])
            result.orphaned.append(orphan_name)
            logger.info(f"Config reconcile: deleted orphan '{orphan_name}'")
        else:
            result.orphaned.append(orphan_name)
            logger.warning(f"Config reconcile: orphan '{orphan_name}' (not in config)")

    # 4. DAG validation — check for cycles in parent_type_name graph
    all_stmt = select(RecordType)
    all_result = await session.execute(all_stmt)
    all_types = all_result.scalars().all()
    edges: dict[str, str | None] = {rt.name: rt.parent_type_name for rt in all_types}
    cycle = detect_cycle(edges)
    if cycle is not None:
        path = " -> ".join(cycle)
        raise ValidationError(f"RecordType parent_type_name graph has a cycle: {path}")

    # 5. Commit
    await session.commit()

    logger.info(
        f"Config reconcile complete: "
        f"{len(result.created)} created, {len(result.updated)} updated, "
        f"{len(result.unchanged)} unchanged, {len(result.orphaned)} orphaned, "
        f"{len(result.errors)} errors"
    )

    return result
