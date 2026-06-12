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
from clarinet.exceptions.domain import ConfigLoadError
from clarinet.models.record import RecordTypeCreate
from clarinet.utils.logger import logger

# Fields whose values can reference external .py files
_SCRIPT_FIELDS = ("slicer_script", "slicer_result_validator")


@contextmanager
def config_sys_path(*dirs: Path) -> Generator[None]:
    """Temporarily put *dirs* on ``sys.path`` for config-file imports.

    Pass directories lowest-priority first (config root, then the file's
    parent): each is inserted at position 0, so the last argument wins
    module lookup. Directories already on ``sys.path`` are skipped — and
    left in place on exit; only the entries inserted here are removed.
    """
    added: list[str] = []
    for d in dirs:
        d_str = str(Path(d).resolve())
        if d_str not in sys.path:
            sys.path.insert(0, d_str)
            added.append(d_str)
    try:
        yield
    finally:
        for d_str in added:
            if d_str in sys.path:
                sys.path.remove(d_str)


def load_module_from_file(name: str, path: Path, *, keep_in_sys: bool = False) -> types.ModuleType:
    """Import *path* as module *name*, failing loudly.

    The module is registered in ``sys.modules`` during execution so sibling
    imports work. On failure it is popped again — a half-initialized module
    must not leak into later imports (test processes run many loads).

    Args:
        name: ``sys.modules`` key (e.g. the file stem for sibling imports,
            or a ``clarinet_custom_*`` namespace for plan/ registries).
        path: Path to the Python file.
        keep_in_sys: If True, leave the module in ``sys.modules`` after
            loading so other modules can import it.

    Raises:
        ConfigLoadError: If the spec cannot be built or executing the file
            raises. The original exception is preserved as ``__cause__``.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ConfigLoadError(f"Cannot create module spec for {path}", path=path)

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        sys.modules.pop(name, None)
        logger.exception(f"Error loading module {path}")
        raise ConfigLoadError(f"Failed to import {path}: {e!r}", path=path) from e

    if not keep_in_sys:
        sys.modules.pop(name, None)

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

    Puts the config root and the ``record_types.py`` parent on ``sys.path``
    (so flow files can use both ``from record_types import ...`` and
    package-style imports like ``from utils.seg_utils import ...``) and
    pre-loads the module.  On exit, removes the added paths and the module
    from ``sys.modules``.

    The caller is still responsible for adding/removing *flow_dir* itself
    from ``sys.path``.

    Resolution order for ``record_types.py``:
    1. ``flow_dir / "record_types.py"``
    2. ``settings.config_tasks_path / settings.config_record_types_file``

    Args:
        flow_dir: Directory containing flow files.

    Raises:
        ConfigLoadError: If ``record_types.py`` exists but fails to import.
    """
    from clarinet.settings import settings

    config_dir = Path(settings.config_tasks_path)

    # Resolve record_types.py: check flow_dir first, then settings fallback
    record_types_file = flow_dir / "record_types.py"
    if not record_types_file.is_file():
        candidate = config_dir.resolve() / settings.config_record_types_file
        if candidate.is_file():
            record_types_file = candidate

    with config_sys_path(config_dir, record_types_file.parent):
        # Pre-load the module so ``from record_types import X`` works
        rt_module_name: str | None = None
        if record_types_file.is_file() and record_types_file.stem not in sys.modules:
            rt_module = load_module_from_file(
                record_types_file.stem, record_types_file, keep_in_sys=True
            )
            rt_module_name = record_types_file.stem
            _set_file_names_from_module(rt_module)

        try:
            yield
        finally:
            if rt_module_name:
                sys.modules.pop(rt_module_name, None)


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


async def _resolve_ui_schema(rt_def: RecordDef, folder: Path) -> dict[str, Any] | None:
    """Resolve ui_schema from a RecordDef (optional — None when absent).

    Resolution order mirrors ``_resolve_data_schema``:
    1. dict — use as-is.
    2. str ending with ``.json`` — read file relative to folder.
    3. None — try sidecar ``{name}.ui_schema.json``.
    """
    schema = rt_def.ui_schema

    if isinstance(schema, dict):
        return schema

    if isinstance(schema, str) and schema.endswith(".json"):
        schema_path = folder / schema
        async with aiofiles.open(schema_path) as f:
            content = await f.read()
        parsed: dict[str, Any] = json.loads(content)
        return parsed

    sidecar = folder / f"{rt_def.name}.ui_schema.json"
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
    # Resolve data_schema and ui_schema
    data_schema = await _resolve_data_schema(rt_def, folder)
    ui_schema = await _resolve_ui_schema(rt_def, folder)

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
    if rt_def.data_validators is not None:
        kwargs["data_validators"] = rt_def.data_validators
    # Only forward mask_patient_data when explicitly set in the RecordDef so the
    # reconciler skips comparison (and preserves DB state) when the field is
    # absent from config — matching the contract of all other optional fields.
    if "mask_patient_data" in rt_def.model_fields_set:
        kwargs["mask_patient_data"] = rt_def.mask_patient_data
    if "unique_per_user" in rt_def.model_fields_set:
        kwargs["unique_per_user"] = rt_def.unique_per_user
    if "parent_required" in rt_def.model_fields_set:
        kwargs["parent_required"] = rt_def.parent_required
    if "inherit_user_from_parent" in rt_def.model_fields_set:
        kwargs["inherit_user_from_parent"] = rt_def.inherit_user_from_parent
    if "editable" in rt_def.model_fields_set:
        kwargs["editable"] = rt_def.editable
    if "edit_window_days" in rt_def.model_fields_set:
        kwargs["edit_window_days"] = rt_def.edit_window_days
    if "viewer_mode" in rt_def.model_fields_set:
        kwargs["viewer_mode"] = rt_def.viewer_mode
    if data_schema is not None:
        kwargs["data_schema"] = data_schema
    if ui_schema is not None:
        kwargs["ui_schema"] = ui_schema
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

    Raises:
        ConfigLoadError: If ``record_types.py`` or ``files_catalog.py``
            fails to import — a broken Python config must crash startup,
            not silently reconcile zero record types.
    """
    from clarinet.settings import settings

    record_types_file = folder / settings.config_record_types_file
    if not record_types_file.is_file():
        logger.warning(f"No {settings.config_record_types_file} found in {folder}")
        return []

    catalog_module_name: str | None = None
    with config_sys_path(folder, record_types_file.parent):
        try:
            # Load files_catalog first (if present) to set FileDef names.
            # Keep it in sys.modules so record_types.py can import it.
            files_catalog_file = folder / settings.config_files_catalog_file
            has_catalog = files_catalog_file.is_file()
            if has_catalog:
                catalog_module = load_module_from_file(
                    files_catalog_file.stem, files_catalog_file, keep_in_sys=True
                )
                catalog_module_name = files_catalog_file.stem
                _set_file_names_from_module(catalog_module)

            # Load record_types module (catalog is available for import)
            module = load_module_from_file(record_types_file.stem, record_types_file)

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
            if catalog_module_name:
                sys.modules.pop(catalog_module_name, None)
