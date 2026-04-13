"""Load RecordType definitions from Python config files.

Discovers ``record_types.py`` in a given folder and collects all
``RecordDef`` instances from its module namespace, converting them
to ``RecordTypeCreate`` objects for the reconciler.

Reuses the importlib pattern from ``clarinet/services/recordflow/flow_loader.py``.
"""

import importlib.util
import json
import sys
import types
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import aiofiles

from clarinet.config.primitives import FileDef, RecordDef, fileref_to_file_definition
from clarinet.models.record import RecordTypeCreate
from clarinet.utils.logger import logger

# Fields whose values can reference external .py files
_SCRIPT_FIELDS = ("slicer_script", "slicer_result_validator")


def _load_module(file_path: Path, *, keep_in_sys: bool = False) -> types.ModuleType | None:
    """Load a Python module from file using importlib.

    The module is registered under its **stem** name (e.g. ``files_catalog``)
    so that sibling modules can import it with ``from files_catalog import X``.

    Args:
        file_path: Path to the Python file.
        keep_in_sys: If True, leave the module in ``sys.modules`` after
            loading so other modules can import it.

    Returns:
        Loaded module, or None on failure.
    """
    # Use the plain stem so sibling imports work (e.g. "files_catalog")
    module_name = file_path.stem
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        logger.error(f"Cannot create module spec for {file_path}")
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.exception(f"Error loading module {file_path}")
        sys.modules.pop(module_name, None)
        return None

    if not keep_in_sys:
        sys.modules.pop(module_name, None)

    return module


def _collect_named_instances(module: types.ModuleType, cls: type[Any]) -> list[tuple[str, Any]]:
    """Collect all instances of *cls* from module namespace with their variable names.

    Args:
        module: Python module to inspect.
        cls: Class to filter instances of.

    Returns:
        List of (variable_name, instance) pairs.
    """
    instances: list[tuple[str, Any]] = []
    for attr_name in dir(module):
        if attr_name.startswith("_"):
            continue
        obj = getattr(module, attr_name)
        if isinstance(obj, cls):
            instances.append((attr_name, obj))
    return instances


def _set_file_names_from_module(module: types.ModuleType) -> None:
    """Set ``name`` on FileDef instances from their variable names in the module.

    Only sets name if it's empty (not explicitly set by user).

    Args:
        module: Python module containing FileDef instances.
    """
    for attr_name, file_obj in _collect_named_instances(module, FileDef):
        if not file_obj.name:
            file_obj.name = attr_name


@contextmanager
def preload_record_types(flow_dir: Path) -> Generator[None]:
    """Resolve and pre-load ``record_types.py`` for flow file imports.

    Manages ``sys.path`` and ``sys.modules`` so that flow files can use
    ``from record_types import ...``.  On exit, cleans up the added paths
    and removes the module from ``sys.modules``.

    The caller is still responsible for adding/removing *flow_dir* itself
    from ``sys.path``.

    Resolution order for ``record_types.py``:
    1. ``flow_dir / "record_types.py"``
    2. ``settings.config_tasks_path / settings.config_record_types_file``

    Args:
        flow_dir: Directory containing flow files.
    """
    from clarinet.settings import settings

    flow_dir_str = str(flow_dir.resolve())

    # Add config_tasks_path so package-style imports work
    # (e.g. ``from utils.seg_utils import ...`` when utils/ is under tasks/)
    config_dir_str = str(Path(settings.config_tasks_path).resolve())
    added_config_dir = config_dir_str != flow_dir_str and config_dir_str not in sys.path
    if added_config_dir:
        sys.path.insert(0, config_dir_str)

    # Resolve record_types.py: check flow_dir first, then settings fallback
    record_types_file = flow_dir / "record_types.py"
    if not record_types_file.is_file():
        config_dir = Path(settings.config_tasks_path).resolve()
        candidate = config_dir / settings.config_record_types_file
        if candidate.is_file():
            record_types_file = candidate

    # If record_types is in a subdirectory, add its parent to sys.path
    rt_parent_str = str(record_types_file.parent.resolve())
    added_rt_parent = rt_parent_str != flow_dir_str and rt_parent_str not in sys.path
    if added_rt_parent:
        sys.path.insert(0, rt_parent_str)

    # Pre-load the module so ``from record_types import X`` works
    rt_module_name: str | None = None
    if record_types_file.is_file() and record_types_file.stem not in sys.modules:
        rt_module = _load_module(record_types_file, keep_in_sys=True)
        if rt_module:
            rt_module_name = record_types_file.stem
            _set_file_names_from_module(rt_module)

    try:
        yield
    finally:
        if rt_module_name:
            sys.modules.pop(rt_module_name, None)
        if added_rt_parent and rt_parent_str in sys.path:
            sys.path.remove(rt_parent_str)
        if added_config_dir and config_dir_str in sys.path:
            sys.path.remove(config_dir_str)


async def _resolve_data_schema(rt_def: RecordDef, folder: Path) -> dict[str, Any] | None:
    """Resolve data_schema from a RecordDef.

    Resolution order:
    1. dict — use as-is.
    2. str ending with ``.json`` — read file relative to folder.
    3. None — try sidecar ``{name}.schema.json``.

    Args:
        rt_def: RecordType definition.
        folder: Config folder for resolving relative paths.

    Returns:
        Parsed JSON schema dict, or None.
    """
    schema = rt_def.data_schema

    if isinstance(schema, dict):
        return schema

    if isinstance(schema, str) and schema.endswith(".json"):
        schema_path = folder / schema
        async with aiofiles.open(schema_path) as f:
            content = await f.read()
        parsed: dict[str, Any] = json.loads(content)
        return parsed

    # Try sidecar
    sidecar = folder / f"{rt_def.name}.schema.json"
    if sidecar.is_file():
        async with aiofiles.open(sidecar) as f:
            content = await f.read()
        sidecar_parsed: dict[str, Any] = json.loads(content)
        return sidecar_parsed

    return None


async def _resolve_script_field(value: str | None, folder: Path) -> str | None:
    """Resolve a .py file reference to inline content.

    Args:
        value: Script value (inline string or .py path).
        folder: Config folder for resolving relative paths.

    Returns:
        Resolved inline script content, or original value.
    """
    if not isinstance(value, str) or not value.endswith(".py"):
        return value
    script_path = folder / value
    if script_path.is_file():
        async with aiofiles.open(script_path) as f:
            return await f.read()
    return value


async def _to_record_type_create(
    rt_def: RecordDef,
    folder: Path,
) -> RecordTypeCreate:
    """Convert a RecordDef to a RecordTypeCreate.

    Resolves data_schema and script file references before constructing
    the typed model.

    Args:
        rt_def: RecordType definition from Python config.
        folder: Config folder for resolving relative paths.

    Returns:
        Typed RecordTypeCreate ready for reconciliation.
    """
    # Resolve data_schema
    data_schema = await _resolve_data_schema(rt_def, folder)

    # Resolve script fields
    slicer_script = await _resolve_script_field(rt_def.slicer_script, folder)
    slicer_result_validator = await _resolve_script_field(rt_def.slicer_result_validator, folder)

    # Convert FileRef list to file_registry
    file_registry = [fileref_to_file_definition(ref) for ref in rt_def.files] or None

    # Build kwargs, only including fields that are explicitly set
    kwargs: dict[str, Any] = {
        "name": rt_def.name,
        "level": rt_def.level,
    }

    if rt_def.description is not None:
        kwargs["description"] = rt_def.description
    if rt_def.label is not None:
        kwargs["label"] = rt_def.label
    if rt_def.role_name is not None:
        kwargs["role_name"] = rt_def.role_name
    if rt_def.min_records is not None:
        kwargs["min_records"] = rt_def.min_records
    if rt_def.max_records is not None:
        kwargs["max_records"] = rt_def.max_records
    if slicer_script is not None:
        kwargs["slicer_script"] = slicer_script
    if rt_def.slicer_script_args is not None:
        kwargs["slicer_script_args"] = rt_def.slicer_script_args
    if slicer_result_validator is not None:
        kwargs["slicer_result_validator"] = slicer_result_validator
    if rt_def.slicer_result_validator_args is not None:
        kwargs["slicer_result_validator_args"] = rt_def.slicer_result_validator_args
    if rt_def.slicer_context_hydrators is not None:
        kwargs["slicer_context_hydrators"] = rt_def.slicer_context_hydrators
    # Only forward mask_patient_data when explicitly set in the RecordDef so the
    # reconciler skips comparison (and preserves DB state) when the field is
    # absent from config — matching the contract of all other optional fields.
    if "mask_patient_data" in rt_def.model_fields_set:
        kwargs["mask_patient_data"] = rt_def.mask_patient_data
    if "viewer_mode" in rt_def.model_fields_set:
        kwargs["viewer_mode"] = rt_def.viewer_mode
    if data_schema is not None:
        kwargs["data_schema"] = data_schema
    if file_registry is not None:
        kwargs["file_registry"] = file_registry

    return RecordTypeCreate(**kwargs)


async def load_python_config(folder: Path) -> list[RecordTypeCreate]:
    """Load RecordType definitions from Python files in *folder*.

    Expected folder structure::

        folder/
            files_catalog.py   # FileDef instances (optional)
            record_types.py    # RecordDef instances

    If ``files_catalog.py`` is absent, FileDef instances in
    ``record_types.py`` get their names auto-derived (single-file mode).

    ``record_types.py`` imports from ``files_catalog.py`` to reference
    shared FileDef objects.

    Args:
        folder: Path to the folder containing Python config files.

    Returns:
        List of RecordTypeCreate objects ready for reconciliation.
    """
    from clarinet.settings import settings

    record_types_file = folder / settings.config_record_types_file
    if not record_types_file.is_file():
        logger.warning(f"No {settings.config_record_types_file} found in {folder}")
        return []

    # Add folder to sys.path temporarily so imports work
    folder_str = str(folder.resolve())
    added_to_path = folder_str not in sys.path
    if added_to_path:
        sys.path.insert(0, folder_str)

    # If record_types_file is in a subdirectory, add its parent too
    rt_parent = str(record_types_file.parent.resolve())
    added_rt_parent = rt_parent != folder_str and rt_parent not in sys.path
    if added_rt_parent:
        sys.path.insert(0, rt_parent)

    catalog_module_name: str | None = None
    try:
        # Load files_catalog first (if present) to set FileDef names.
        # Keep it in sys.modules so record_types.py can import it.
        files_catalog_file = folder / settings.config_files_catalog_file
        has_catalog = files_catalog_file.is_file()
        if has_catalog:
            catalog_module = _load_module(files_catalog_file, keep_in_sys=True)
            if catalog_module:
                catalog_module_name = files_catalog_file.stem
                _set_file_names_from_module(catalog_module)

        # Load record_types module (catalog is available for import)
        module = _load_module(record_types_file)
        if module is None:
            return []

        # Single-file mode: set file names from record_types.py itself
        if not has_catalog:
            _set_file_names_from_module(module)

        # Collect RecordDef instances
        rt_defs = _collect_named_instances(module, RecordDef)
        if not rt_defs:
            logger.warning(f"No RecordDef instances found in {record_types_file}")
            return []

        logger.info(f"Found {len(rt_defs)} RecordDef(s) in {record_types_file}")

        # Convert to RecordTypeCreate objects
        result: list[RecordTypeCreate] = []
        for _var_name, rt_def in rt_defs:
            config_item = await _to_record_type_create(rt_def, folder)
            result.append(config_item)

        return result

    finally:
        # Clean up sys.modules and sys.path
        if catalog_module_name:
            sys.modules.pop(catalog_module_name, None)
        if added_rt_parent and rt_parent in sys.path:
            sys.path.remove(rt_parent)
        if added_to_path and folder_str in sys.path:
            sys.path.remove(folder_str)
