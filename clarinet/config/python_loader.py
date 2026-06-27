"""Load RecordType definitions from Python config files.

Discovers ``record_types.py`` in a given folder and collects all
``RecordDef`` instances from its module namespace, converting them
to ``RecordTypeCreate`` objects for the reconciler.

Imports go through the ``clarinet_plan`` anchor package
(``clarinet/config/plan_package.py``) — no ``sys.path`` manipulation.
Contract: ``.claude/rules/custom-code-loading.md``.
"""

import json
import types
from pathlib import Path
from typing import Any

import aiofiles

from clarinet.config.primitives import FileDef, RecordDef, fileref_to_file_definition
from clarinet.models.record import RecordTypeCreate
from clarinet.utils.logger import logger
from clarinet.utils.schema_bundler import bundle_external_defs

# Fields whose values can reference external .py files
_SCRIPT_FIELDS = ("slicer_script", "slicer_result_validator")


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
        return bundle_external_defs(parsed, schema_path.parent)

    # Try sidecar
    sidecar = folder / f"{rt_def.name}.schema.json"
    if sidecar.is_file():
        async with aiofiles.open(sidecar) as f:
            content = await f.read()
        sidecar_parsed: dict[str, Any] = json.loads(content)
        return bundle_external_defs(sidecar_parsed, folder)

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
    if "shared_editing" in rt_def.model_fields_set:
        kwargs["shared_editing"] = rt_def.shared_editing
    if "edit_window_days" in rt_def.model_fields_set:
        kwargs["edit_window_days"] = rt_def.edit_window_days
    if "viewer_mode" in rt_def.model_fields_set:
        kwargs["viewer_mode"] = rt_def.viewer_mode
    if rt_def.allowed_viewers is not None:
        kwargs["allowed_viewers"] = rt_def.allowed_viewers
    if data_schema is not None:
        kwargs["data_schema"] = data_schema
    if ui_schema is not None:
        kwargs["ui_schema"] = ui_schema
    if file_registry is not None:
        kwargs["file_registry"] = file_registry

    return RecordTypeCreate(**kwargs)


def _ensure_record_types_imported(folder: Path | None = None) -> None:
    """Import the catalog + ``record_types`` modules and set FileDef names.

    Ordering is strict and matters:

    1. import ``clarinet_plan.<files_catalog>`` (if the file exists);
    2. set FileDef names from the catalog module;
    3. import ``clarinet_plan.<record_types>`` (if the file exists);
    4. single-file fallback — no catalog → set FileDef names from
       ``record_types`` itself.

    Idempotent: ``import_plan_module`` returns the cached module on re-entry,
    and ``_set_file_names_from_module`` only fills *empty* names.

    Must run after the anchor is active (it calls ``ensure_plan_root`` itself)
    and **before** any plan file that transitively imports ``record_types``
    (e.g. ``validators.py``) — otherwise module-level reads of ``FileDef.name``
    would see ``""`` because the names are assigned here.

    Args:
        folder: Config root (defaults to ``settings.config_tasks_path``).

    Raises:
        ConfigLoadError: If the catalog or ``record_types`` file fails to import.
    """
    from clarinet.config.plan_package import ensure_plan_root, import_plan_module, module_name_for
    from clarinet.settings import settings

    root = Path(folder) if folder is not None else Path(settings.config_tasks_path)
    ensure_plan_root(root)

    catalog_file = root / settings.config_files_catalog_file
    has_catalog = catalog_file.is_file()
    if has_catalog:
        catalog = import_plan_module(module_name_for(catalog_file), path_hint=catalog_file)
        _set_file_names_from_module(catalog)

    record_types_file = root / settings.config_record_types_file
    if record_types_file.is_file():
        rt = import_plan_module(module_name_for(record_types_file), path_hint=record_types_file)
        if not has_catalog:
            _set_file_names_from_module(rt)


async def load_python_config(folder: Path) -> list[RecordTypeCreate]:
    """Load RecordType definitions from Python files in *folder*.

    Expected folder structure::

        folder/
            files_catalog.py   # FileDef instances (optional)
            record_types.py    # RecordDef instances

    If ``files_catalog.py`` is absent, FileDef instances in
    ``record_types.py`` get their names auto-derived (single-file mode).

    ``record_types.py`` imports from ``files_catalog.py`` via the
    ``clarinet_plan.`` prefix (or a relative import) to reference shared FileDef
    objects.

    Args:
        folder: Path to the folder containing Python config files.

    Returns:
        List of RecordTypeCreate objects ready for reconciliation.

    Raises:
        ConfigLoadError: If ``record_types.py`` or ``files_catalog.py``
            fails to import — a broken Python config must crash startup,
            not silently reconcile zero record types.
    """
    from clarinet.config.plan_package import import_plan_module, module_name_for
    from clarinet.settings import settings

    record_types_file = folder / settings.config_record_types_file
    if not record_types_file.is_file():
        logger.warning(f"No {settings.config_record_types_file} found in {folder}")
        return []

    # Imports catalog + record_types and assigns FileDef names (idempotent).
    _ensure_record_types_imported(folder)

    module = import_plan_module(module_name_for(record_types_file), path_hint=record_types_file)

    rt_defs = _collect_named_instances(module, RecordDef)
    if not rt_defs:
        logger.warning(f"No RecordDef instances found in {record_types_file}")
        return []

    logger.info(f"Found {len(rt_defs)} RecordDef(s) in {record_types_file}")

    result: list[RecordTypeCreate] = []
    for _var_name, rt_def in rt_defs:
        config_item = await _to_record_type_create(rt_def, folder)
        result.append(config_item)

    return result
