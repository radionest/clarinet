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


def load_flows_from_file(file_path: Path) -> list[FlowRecord | FlowFileRecord]:
    """
    Load FlowRecord definitions from a Python file.

    The file should contain FlowRecord definitions using the record() DSL.
    The flows are automatically registered in RECORD_REGISTRY when the
    file is executed.

    Before loading, adds the parent directory to ``sys.path`` and pre-loads
    ``record_types.py`` (if present as a sibling) so that flow files can use
    ``from record_types import master_model`` for cross-record-type file access.

    Args:
        file_path: Path to the Python file containing flow definitions.

    Returns:
        List of FlowRecord instances found in the file.

    Raises:
        ConfigLoadError: If the flow file (or its ``record_types.py``
            dependency) fails to import.

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

    # Clear the registries before loading a new file to avoid duplicates
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    call_function_registry.reset()

    from clarinet.config.python_loader import (
        config_sys_path,
        load_module_from_file,
        preload_record_types,
    )

    # Parent dir on sys.path for sibling imports; record_types.py pre-loaded
    parent_dir = file_path.parent
    with config_sys_path(parent_dir), preload_record_types(parent_dir):
        # keep_in_sys: later flow files may ``import`` this one (cross-flow
        # imports). Without the cache entry Python would re-execute the file
        # from disk and its flows would register a second time.
        load_module_from_file(file_path.stem, file_path, keep_in_sys=True)

        # Return only active flows (filter out reference-only FlowRecords
        # created for data access like record('type').data.field)
        # Entity flows always have entity_trigger set, so they pass is_active_flow()
        record_flows = [f for f in RECORD_REGISTRY if f.is_active_flow()]
        entity_flows = list(ENTITY_REGISTRY)
        file_flows = [f for f in FILE_REGISTRY if f.is_active_flow()]
        flows: list[FlowRecord | FlowFileRecord] = record_flows + entity_flows + file_flows
        for flow in flows:
            logger.info(f"Loaded flow: {flow!r}")

        return flows


def load_and_register_flows(engine: RecordFlowEngine, flow_files: list[Path]) -> int:
    """
    Load flows from multiple files and register them with the engine.

    Args:
        engine: RecordFlowEngine instance to register flows with.
        flow_files: List of paths to flow definition files.

    Returns:
        Total number of flows registered.

    Raises:
        ConfigLoadError: Aggregated error when any flow file fails to
            import. Every file is attempted first, so one startup crash
            reports all broken files at once.
    """
    total_flows = 0
    failures: list[ConfigLoadError] = []

    for file_path in flow_files:
        try:
            flows = load_flows_from_file(file_path)
        except ConfigLoadError as e:
            failures.append(e)
            continue
        for flow in flows:
            try:
                engine.register_flow(flow)
                total_flows += 1
            except Exception as e:
                logger.error(f"Error registering flow {flow!r}: {e}")

    if failures:
        raise ConfigLoadError.aggregate(failures, kind="flow file")

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
