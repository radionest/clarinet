"""Reconcile RecordType definitions from config files with the database.

Compares a list of config-derived property dicts against existing DB rows,
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

from src.models.file_schema import RecordTypeFileLink
from src.models.record import RecordType, RecordTypeCreate
from src.repositories.file_definition_repository import FileDefinitionRepository
from src.utils.logger import logger

# Fields compared when detecting changes between config and DB.
# file_registry is handled separately via link diff.
_COMPARED_FIELDS: tuple[str, ...] = (
    "description",
    "label",
    "level",
    "role_name",
    "min_users",
    "max_users",
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
    if isinstance(value, list) and not value:
        return None
    if isinstance(value, dict) and not value:
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


def _fields_differ(db_record_type: RecordType, config_props: dict[str, Any]) -> list[str]:
    """Return list of scalar field names that differ between DB and config.

    Does NOT compare file_registry — that is handled separately via link diff.

    Args:
        db_record_type: Existing RecordType from DB.
        config_props: Config properties dict.

    Returns:
        List of field names that have different values.
    """
    changed: list[str] = []
    for field_name in _COMPARED_FIELDS:
        if field_name not in config_props:
            continue
        db_val = _normalize(getattr(db_record_type, field_name, None))
        config_val = _normalize(config_props.get(field_name))
        if db_val != config_val:
            if config_val is None and db_val == _get_field_default(field_name):
                continue
            changed.append(field_name)
    return changed


def _extract_file_defs(
    config_props: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Extract file definition dicts from config props.

    Returns None if file_registry is not in props (meaning: don't touch files).
    Returns empty list if explicitly set to empty.
    """
    if "file_registry" not in config_props:
        return None

    raw = config_props["file_registry"]
    if not raw:
        return []

    result: list[dict[str, Any]] = []
    for item in raw:
        if hasattr(item, "model_dump"):
            result.append(item.model_dump())
        elif isinstance(item, dict):
            result.append(item)
        else:
            result.append(dict(item))
    return result


def _file_links_differ(
    existing_links: list[RecordTypeFileLink],
    config_defs: list[dict[str, Any]],
) -> bool:
    """Check whether the file link set differs from config definitions."""
    if len(existing_links) != len(config_defs):
        return True

    # Build comparable sets
    existing_set: set[tuple[str, str, bool]] = set()
    for link in existing_links:
        role_val = link.role.value if hasattr(link.role, "value") else str(link.role)
        existing_set.add((link.file_definition.name, role_val, link.required))

    config_set: set[tuple[str, str, bool]] = set()
    for d in config_defs:
        role = d.get("role", "output")
        if hasattr(role, "value"):
            role = role.value
        config_set.add((d["name"], str(role), d.get("required", True)))

    return existing_set != config_set


async def _sync_file_links(
    record_type: RecordType,
    config_defs: list[dict[str, Any]],
    fd_repo: FileDefinitionRepository,
    session: AsyncSession,
) -> None:
    """Synchronize file links for a RecordType from config definitions.

    Removes old links, upserts FileDefinitions, and creates new links.

    Args:
        record_type: RecordType to sync links for.
        config_defs: List of flat file definition dicts.
        fd_repo: FileDefinitionRepository for upserting.
        session: Database session.
    """
    # Remove existing links
    for link in list(record_type.file_links or []):
        await session.delete(link)
    await session.flush()

    if not config_defs:
        record_type.file_links = []
        return

    # Upsert FileDefinitions
    fd_data = [
        {
            "name": d["name"],
            "pattern": d["pattern"],
            "description": d.get("description"),
            "multiple": d.get("multiple", False),
        }
        for d in config_defs
    ]
    fd_map = await fd_repo.bulk_upsert(fd_data)

    # Create new links
    new_links: list[RecordTypeFileLink] = []
    for d in config_defs:
        fd = fd_map[d["name"]]
        role = d.get("role", "output")
        link = RecordTypeFileLink(
            record_type_name=record_type.name,
            file_definition_id=fd.id,  # type: ignore[arg-type]
            role=role,
            required=d.get("required", True),
        )
        session.add(link)
        new_links.append(link)

    await session.flush()

    # Refresh links so they're fully loaded
    record_type.file_links = new_links


async def reconcile_record_types(
    config_props_list: list[dict[str, Any]],
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
        config_props_list: List of property dicts compatible with RecordTypeCreate.
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
    for props in config_props_list:
        name = props.get("name", "")
        config_names.add(name)

        try:
            config_defs = _extract_file_defs(props)

            if name not in db_types:
                # CREATE — new RecordType
                # Build create props WITHOUT file_registry (handled via links)
                create_props = {k: v for k, v in props.items() if k != "file_registry"}
                create_schema = RecordTypeCreate(**create_props)
                new_rt = RecordType.model_validate(create_schema)
                new_rt.file_links = []  # Initialize empty, will be populated below
                session.add(new_rt)
                await session.flush()

                # Sync file links
                if config_defs:
                    await _sync_file_links(new_rt, config_defs, fd_repo, session)

                result.created.append(name)
                logger.info(f"Config reconcile: created '{name}'")
            else:
                # DIFF — check scalar fields and file links
                existing = db_types[name]
                changed_fields = _fields_differ(existing, props)

                files_changed = False
                if config_defs is not None:
                    files_changed = _file_links_differ(existing.file_links or [], config_defs)

                if changed_fields or files_changed:
                    # Update scalar fields
                    for field_name in changed_fields:
                        setattr(existing, field_name, props.get(field_name))

                    # Sync file links if they changed
                    if files_changed and config_defs is not None:
                        await _sync_file_links(existing, config_defs, fd_repo, session)

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

    # 4. Commit
    await session.commit()

    logger.info(
        f"Config reconcile complete: "
        f"{len(result.created)} created, {len(result.updated)} updated, "
        f"{len(result.unchanged)} unchanged, {len(result.orphaned)} orphaned, "
        f"{len(result.errors)} errors"
    )

    return result
