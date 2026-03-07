"""Config loader for RecordType definitions.

Supports TOML (.toml, preferred) and JSON (.json) config files with file
references for scripts (.py) and schemas (.json). When both formats exist
for the same stem, TOML takes precedence.
"""

import json
import tomllib
from pathlib import Path
from typing import Any

import aiofiles

from clarinet.utils.logger import logger

# Fields whose values can reference external .py files
_SCRIPT_FIELDS = ("slicer_script", "slicer_result_validator")

# Files excluded from config discovery
_EXCLUDED_NAMES = {"file_registry.json", "file_registry.toml"}


def discover_config_files(folder: str, suffix_filter: str = "") -> list[Path]:
    """Scan *folder* for TOML/JSON config files, TOML taking precedence.

    Args:
        folder: Directory to scan for config files.
        suffix_filter: If non-empty, only include configs whose stem contains
            this substring (same semantics as the old ``filter_record_schemas``).

    Returns:
        Sorted list of config file paths.
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        logger.warning(f"Config folder {folder} not found")
        return []

    toml_files: dict[str, Path] = {}
    json_files: dict[str, Path] = {}

    for path in folder_path.iterdir():
        if not path.is_file():
            continue
        if path.name in _EXCLUDED_NAMES:
            continue
        # Skip schema sidecars
        if path.name.endswith(".schema.json"):
            continue

        stem = path.stem
        if suffix_filter and suffix_filter not in stem:
            continue

        if path.suffix == ".toml":
            toml_files[stem] = path
        elif path.suffix == ".json":
            json_files[stem] = path

    # TOML takes precedence: only include JSON when no TOML exists for that stem
    result: dict[str, Path] = {}
    for stem, path in toml_files.items():
        result[stem] = path
    for stem, path in json_files.items():
        if stem not in result:
            result[stem] = path

    return sorted(result.values(), key=lambda p: p.stem)


async def load_record_config(config_path: Path) -> dict[str, Any] | None:
    """Load a RecordType config from a TOML or JSON file.

    Resolves file references (.py scripts, .json schemas) and sidecar
    schemas relative to the config file's directory.

    Args:
        config_path: Path to a ``.toml`` or ``.json`` config file.

    Returns:
        Properties dict ready for ``resolve_task_files()`` then
        ``RecordTypeCreate()``, or ``None`` if no schema could be found.

    Raises:
        ValueError: If the file extension is unsupported.
    """
    if config_path.suffix == ".toml":
        props = await _load_toml(config_path)
    elif config_path.suffix == ".json":
        props = await _load_json(config_path)
    else:
        raise ValueError(f"Unsupported config format: {config_path.suffix}")

    config_dir = config_path.parent
    props = await _resolve_file_references(props, config_dir)
    resolved = await _resolve_data_schema(props, config_dir, config_path.stem)
    if resolved is None:
        logger.warning(f"Cannot find schema for record type config {config_path.name}")
        return None
    return resolved


async def _load_toml(config_path: Path) -> dict[str, Any]:
    """Read and parse a TOML config file.

    Args:
        config_path: Path to the TOML file.

    Returns:
        Parsed dict.
    """
    async with aiofiles.open(config_path) as f:
        content = await f.read()
    return tomllib.loads(content)


async def _load_json(config_path: Path) -> dict[str, Any]:
    """Read and parse a JSON config file.

    Args:
        config_path: Path to the JSON file.

    Returns:
        Parsed dict.
    """
    async with aiofiles.open(config_path) as f:
        content = await f.read()
    result: dict[str, Any] = json.loads(content)
    return result


async def _resolve_file_references(props: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    """Resolve external .py file references to inline content.

    For ``slicer_script`` and ``slicer_result_validator``: if the value is
    a string ending with ``.py``, read the file (relative to *config_dir*)
    and replace the value with its content.  Non-.py strings are left as-is
    (treated as inline code).

    Args:
        props: Config properties dict (mutated in place).
        config_dir: Directory containing the config file.

    Returns:
        The mutated *props* dict.
    """
    for field in _SCRIPT_FIELDS:
        value = props.get(field)
        if not isinstance(value, str):
            continue
        if not value.endswith(".py"):
            continue
        script_path = config_dir / value
        async with aiofiles.open(script_path) as f:
            props[field] = await f.read()
    return props


async def _resolve_data_schema(
    props: dict[str, Any],
    config_dir: Path,
    stem: str,
) -> dict[str, Any] | None:
    """Resolve the data schema from various sources.

    Resolution order:
    1. ``data_schema`` is a str ending ``.json`` — read and parse that file.
    2. ``data_schema`` is a dict (inline TOML table or parsed JSON) — keep as-is.
    3. ``result_schema`` present (legacy) — rename to ``data_schema``.
    4. Absent — try ``{stem}.schema.json`` sidecar.
    5. Nothing found — return ``None``.

    Args:
        props: Config properties dict (mutated in place).
        config_dir: Directory containing the config file.
        stem: Config file stem (e.g. ``"lesion_seg"``).

    Returns:
        The mutated *props* dict, or ``None`` if no schema was found.
    """
    data_schema = props.get("data_schema")

    # 1. String reference to .json file
    if isinstance(data_schema, str) and data_schema.endswith(".json"):
        schema_path = config_dir / data_schema
        async with aiofiles.open(schema_path) as f:
            props["data_schema"] = json.loads(await f.read())
        return props

    # 2. Inline dict (TOML table or parsed JSON object)
    if isinstance(data_schema, dict):
        return props

    # 3. Legacy: result_schema → data_schema
    if props.get("result_schema") is not None:
        props["data_schema"] = props.pop("result_schema")
        return props

    # 4. Sidecar schema file
    sidecar = config_dir / f"{stem}.schema.json"
    if sidecar.is_file():
        async with aiofiles.open(sidecar) as f:
            props["data_schema"] = json.loads(await f.read())
        return props

    # 5. Nothing found
    return None
