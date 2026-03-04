"""Reconcile RecordType definitions from config files with the database.

Compares a list of config-derived property dicts against existing DB rows,
creating new RecordTypes, updating changed ones, and optionally deleting
orphans no longer in the config.
"""

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.models.record import RecordType, RecordTypeCreate
from src.utils.logger import logger

# Fields compared when detecting changes between config and DB.
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
    "file_registry",
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

    Enums are converted to their string value, empty lists/dicts become None,
    and file_registry lists are sorted by name for stable comparison.
    """
    if hasattr(value, "value"):
        # Enum → string
        return value.value
    if isinstance(value, list):
        if not value:
            return None
        # Normalize list of dicts (file_registry) — sort by name
        normalized = []
        for item in value:
            if hasattr(item, "model_dump"):
                normalized.append(item.model_dump())
            elif isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append(item)
        if normalized and isinstance(normalized[0], dict) and "name" in normalized[0]:
            normalized = sorted(normalized, key=lambda d: d.get("name", ""))
        return normalized
    if isinstance(value, dict) and not value:
        return None
    return value


def _fields_differ(db_record_type: RecordType, config_props: dict[str, Any]) -> list[str]:
    """Return list of field names that differ between DB and config.

    Only compares fields that are explicitly present in *config_props*.
    Missing fields are assumed unchanged (keeps DB value).

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
            changed.append(field_name)
    return changed


async def reconcile_record_types(
    config_props_list: list[dict[str, Any]],
    session: AsyncSession,
    *,
    delete_orphans: bool = False,
) -> ReconcileResult:
    """Diff config definitions against DB and apply changes.

    Algorithm:
    1. Load all existing RecordTypes from DB.
    2. For each config entry: CREATE if new, UPDATE if changed, skip if identical.
    3. DB names not in config → orphans (warn or delete).
    4. Single commit at the end.

    Args:
        config_props_list: List of property dicts compatible with RecordTypeCreate.
        session: Async database session.
        delete_orphans: If True, delete DB RecordTypes not in config.

    Returns:
        ReconcileResult with counts per category.
    """
    result = ReconcileResult()

    # 1. Load existing RecordTypes
    stmt = select(RecordType)
    db_result = await session.execute(stmt)
    db_types: dict[str, RecordType] = {rt.name: rt for rt in db_result.scalars().all()}

    config_names: set[str] = set()

    # 2. Process each config entry
    for props in config_props_list:
        name = props.get("name", "")
        config_names.add(name)

        try:
            if name not in db_types:
                # CREATE — ensure file_registry is stored as plain dicts
                create_props = dict(props)
                if create_props.get("file_registry"):
                    create_props["file_registry"] = [
                        fd.model_dump() if hasattr(fd, "model_dump") else fd
                        for fd in create_props["file_registry"]
                    ]
                create_schema = RecordTypeCreate(**create_props)
                new_rt = RecordType.model_validate(create_schema)
                # Ensure file_registry is serializable as JSON
                if new_rt.file_registry:
                    serialized = [
                        fd.model_dump() if hasattr(fd, "model_dump") else fd
                        for fd in new_rt.file_registry
                    ]
                    new_rt.file_registry = serialized  # type: ignore[assignment]
                session.add(new_rt)
                result.created.append(name)
                logger.info(f"Config reconcile: created '{name}'")
            else:
                # DIFF
                existing = db_types[name]
                changed_fields = _fields_differ(existing, props)
                if changed_fields:
                    for field_name in changed_fields:
                        setattr(existing, field_name, props.get(field_name))
                    result.updated.append(name)
                    logger.info(
                        f"Config reconcile: updated '{name}' (fields: {', '.join(changed_fields)})"
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
