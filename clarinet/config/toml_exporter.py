"""Export RecordType definitions from DB to TOML files.

Provides bidirectional sync for TOML config mode: when a RecordType
is created or updated via the API, the corresponding TOML file and
optional JSON schema sidecar are written to the config folder.
"""

import json
from pathlib import Path
from typing import Any

import aiofiles
import tomli_w

from clarinet.models.record import RecordType
from clarinet.utils.logger import logger

# Scalar fields exported to TOML top-level keys.
_SCALAR_FIELDS: tuple[str, ...] = (
    "name",
    "description",
    "label",
    "level",
    "parent_type_name",
    "role_name",
    "min_records",
    "max_records",
    "slicer_script",
    "slicer_result_validator",
)

# Dict fields exported as TOML tables.
_TABLE_FIELDS: tuple[str, ...] = (
    "slicer_script_args",
    "slicer_result_validator_args",
)


def _record_type_to_toml_dict(rt: RecordType) -> dict[str, Any]:
    """Convert a RecordType to a TOML-serializable dict.

    Args:
        rt: RecordType model instance.

    Returns:
        Dict suitable for ``tomli_w.dumps()``.
    """
    data: dict[str, Any] = {}

    # Scalar fields
    for field_name in _SCALAR_FIELDS:
        value = getattr(rt, field_name, None)
        if value is None:
            continue
        # Enum → string
        if hasattr(value, "value"):
            value = value.value
        data[field_name] = value

    # Table fields (dicts)
    for field_name in _TABLE_FIELDS:
        value = getattr(rt, field_name, None)
        if value:
            data[field_name] = value

    # File registry → [[file_registry]] array of tables
    file_registry = rt.file_registry
    if file_registry:
        files: list[dict[str, Any]] = []
        for item in file_registry:
            fd = item.model_dump(mode="json")
            file_entry: dict[str, Any] = {"name": fd["name"]}
            if fd.get("pattern"):
                file_entry["pattern"] = fd["pattern"]
            if fd.get("role"):
                file_entry["role"] = fd["role"]
            if fd.get("required") is not None:
                file_entry["required"] = fd["required"]
            if fd.get("description"):
                file_entry["description"] = fd["description"]
            if fd.get("multiple"):
                file_entry["multiple"] = True
            if fd.get("level"):
                file_entry["level"] = fd["level"]
            files.append(file_entry)
        if files:
            data["file_registry"] = files

    return data


async def export_record_type_to_toml(rt: RecordType, folder: Path) -> Path:
    """Write a RecordType to a TOML file in *folder*.

    The file is named ``{rt.name}.toml``. ``data_schema`` is written
    as a separate JSON sidecar (see ``export_data_schema_sidecar``).

    Args:
        rt: RecordType to export.
        folder: Target directory for the TOML file.

    Returns:
        Path to the written TOML file.
    """
    folder.mkdir(parents=True, exist_ok=True)

    toml_dict = _record_type_to_toml_dict(rt)
    toml_bytes = tomli_w.dumps(toml_dict)

    toml_path = folder / f"{rt.name}.toml"
    async with aiofiles.open(toml_path, "w") as f:
        await f.write(toml_bytes)

    logger.info(f"Exported RecordType '{rt.name}' to {toml_path}")
    return toml_path


async def export_data_schema_sidecar(rt: RecordType, folder: Path) -> Path | None:
    """Write the data_schema as a JSON sidecar file.

    The file is named ``{rt.name}.schema.json``. If ``data_schema`` is
    empty or None, no file is written.

    Args:
        rt: RecordType whose schema to export.
        folder: Target directory for the schema file.

    Returns:
        Path to the written file, or None if no schema exists.
    """
    if not rt.data_schema:
        return None

    folder.mkdir(parents=True, exist_ok=True)

    schema_path = folder / f"{rt.name}.schema.json"
    content = json.dumps(rt.data_schema, indent=2, ensure_ascii=False)
    async with aiofiles.open(schema_path, "w") as f:
        await f.write(content)

    logger.info(f"Exported data schema for '{rt.name}' to {schema_path}")
    return schema_path


async def delete_record_type_files(name: str, folder: Path) -> list[Path]:
    """Delete TOML and schema sidecar files for a RecordType.

    Args:
        name: RecordType name.
        folder: Directory containing config files.

    Returns:
        List of paths that were deleted.
    """
    deleted: list[Path] = []

    toml_path = folder / f"{name}.toml"
    if toml_path.is_file():
        toml_path.unlink()
        deleted.append(toml_path)
        logger.info(f"Deleted config file {toml_path}")

    schema_path = folder / f"{name}.schema.json"
    if schema_path.is_file():
        schema_path.unlink()
        deleted.append(schema_path)
        logger.info(f"Deleted schema sidecar {schema_path}")

    return deleted
