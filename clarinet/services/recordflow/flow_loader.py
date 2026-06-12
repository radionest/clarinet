"""
Module for loading record flow definitions from Python files.

This module provides functions to load FlowRecord definitions from external
Python files, enabling dynamic flow configuration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import ConfigLoadError
from clarinet.utils.logger import logger

from . import call_function_registry
from .engine import RecordFlowEngine
from .flow_file import FILE_REGISTRY
from .flow_record import ENTITY_REGISTRY, RECORD_REGISTRY

if TYPE_CHECKING:
    from .flow_file import FlowFileRecord
    from .flow_record import FlowRecord


def _clear_flow_registries() -> None:
    """Reset the four flow registries before a load cycle.

    Includes ``call_function_registry.reset()``: a per-file reset (the old
    behaviour) erased the ``.call()`` callbacks of every earlier file in a
    multi-file project — a latent bug. Clearing **once per load cycle** (not per
    file) preserves callbacks across files.
    """
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    call_function_registry.reset()


def _collect_flows() -> list[FlowRecord | FlowFileRecord]:
    """Gather active flows from the registries after one or more imports.

    Filters out reference-only ``FlowRecord``s (created for data access like
    ``record('type').data.field``). Entity flows always carry an entity trigger,
    so they pass ``is_active_flow()``.
    """
    record_flows = [f for f in RECORD_REGISTRY if f.is_active_flow()]
    entity_flows = list(ENTITY_REGISTRY)
    file_flows = [f for f in FILE_REGISTRY if f.is_active_flow()]
    return [*record_flows, *entity_flows, *file_flows]


def _import_flow_file(file_path: Path) -> None:
    """Import one flow file as a ``clarinet_plan.`` submodule.

    The flows register into the module-level registries as a side effect of the
    import. The native module cache makes execution exactly-once, so a flow file
    may import any sibling (in either sort direction) without re-execution.
    """
    from clarinet.config.plan_package import (
        ensure_plan_root,
        import_plan_module,
        module_name_for,
    )

    ensure_plan_root(file_path.parent)
    import_plan_module(module_name_for(file_path), path_hint=file_path)


def load_flows_from_file(file_path: Path) -> list[FlowRecord | FlowFileRecord]:
    """
    Load FlowRecord definitions from a single Python file.

    The file contains FlowRecord definitions using the record() DSL; the flows
    register into RECORD_REGISTRY when the file is imported. Imported as a
    ``clarinet_plan.`` submodule — flow files reference record types via
    ``from clarinet_plan.record_types import master_model`` (or relative).

    Standalone single-file cycle (clear → import → collect) for direct
    calls/tests. The multi-file path is ``load_and_register_flows``.

    Args:
        file_path: Path to the Python file containing flow definitions.

    Returns:
        List of FlowRecord instances found in the file.

    Raises:
        ConfigLoadError: If the flow file fails to import.

    Example file content:
        from clarinet.services.recordflow import record

        record('doctor_report')
            .on_status('finished')
            .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
            .add_record('confirm_birads')
    """
    if not file_path.exists():
        logger.warning(f"Flow file not found: {file_path}")
        return []

    _clear_flow_registries()
    _import_flow_file(file_path)

    flows = _collect_flows()
    for flow in flows:
        logger.info(f"Loaded flow: {flow!r}")
    return flows


def load_and_register_flows(engine: RecordFlowEngine, flow_files: list[Path]) -> int:
    """
    Load flows from multiple files and register them with the engine.

    Clears the registries **once** before the loop, imports every file, then
    collects flows **once** after all imports. A per-file clear-then-collect
    (the old behaviour) gathered a sibling file's flows in the wrong window and
    silently dropped them when an importer failed *after* a transitive import —
    fixed here.

    Args:
        engine: RecordFlowEngine instance to register flows with.
        flow_files: List of paths to flow definition files.

    Returns:
        Total number of flows registered.

    Raises:
        ConfigLoadError: Aggregated error when any flow file fails to
            import (or lives outside the plan root). Every file is attempted
            first, so one startup crash reports all broken files at once.
    """
    _clear_flow_registries()
    failures: list[ConfigLoadError] = []

    for file_path in flow_files:
        try:
            _import_flow_file(file_path)
        except ConfigLoadError as e:
            failures.append(e)
            continue

    # Raise BEFORE registering: if any file failed, register nothing — a broken
    # file may have registered some flows into the registry before crashing, and
    # those must not reach the engine. (Startup crashes on the aggregate anyway,
    # but this keeps "broken file ⇒ its flows don't register" exact.)
    if failures:
        raise ConfigLoadError.aggregate(failures, kind="flow file")

    total_flows = 0
    for flow in _collect_flows():
        try:
            engine.register_flow(flow)
            total_flows += 1
        except Exception as e:
            logger.error(f"Error registering flow {flow!r}: {e}")

    logger.info(f"Registered {total_flows} flows from {len(flow_files)} files")
    return total_flows


def find_flow_files(base_path: Path, pattern: str = "*_flow.py") -> list[Path]:
    """
    Find flow definition files in a directory.

    Results are sorted by path so the engine sees flows in the same order
    on every process / replica / filesystem. Without this, ``Path.glob``
    yields filesystem order — different across OS/FS — which destabilises
    both flow dispatch order and the ``/api/admin/workflow`` plan digest.

    Args:
        base_path: Base directory to search in.
        pattern: Glob pattern for flow files (default: *_flow.py).

    Returns:
        List of paths to flow definition files, sorted lexicographically.
    """
    flow_files: list[Path] = []

    if base_path.exists() and base_path.is_dir():
        flow_files = sorted(base_path.glob(pattern))
        logger.info(f"Found {len(flow_files)} flow files in {base_path}")

    return flow_files


def discover_and_load_flows(engine: RecordFlowEngine, search_paths: list[Path]) -> int:
    """
    Discover and load all flow files from multiple directories.

    This is a convenience function that combines find_flow_files and
    load_and_register_flows.

    Args:
        engine: RecordFlowEngine instance to register flows with.
        search_paths: List of directories to search for *_flow.py files.

    Returns:
        Total number of flows registered.
    """
    all_flow_files: list[Path] = []

    for search_path in search_paths:
        if search_path.is_file():
            # If it's a file, add it directly
            all_flow_files.append(search_path)
        elif search_path.is_dir():
            # If it's a directory, search for flow files
            all_flow_files.extend(find_flow_files(search_path))

    return load_and_register_flows(engine, all_flow_files)
