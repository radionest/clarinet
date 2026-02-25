"""
Module for loading record flow definitions from Python files.

This module provides functions to load FlowRecord definitions from external
Python files, enabling dynamic flow configuration.
"""

from pathlib import Path

from src.utils.logger import logger

from .engine import RecordFlowEngine
from .flow_record import RECORD_REGISTRY, FlowRecord, record


def load_flows_from_file(file_path: Path) -> list[FlowRecord]:
    """
    Load FlowRecord definitions from a Python file.

    The file should contain FlowRecord definitions using the record() DSL.
    The flows are automatically registered in RECORD_REGISTRY when the
    file is executed.

    Args:
        file_path: Path to the Python file containing flow definitions.

    Returns:
        List of FlowRecord instances found in the file.

    Example file content:
        from src.services.recordflow import record

        record('doctor_report')
            .on_status('finished')
            .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
            .add_record('confirm_birads')
    """
    if not file_path.exists():
        logger.warning(f"Flow file not found: {file_path}")
        return []

    # Clear the registry before loading a new file to avoid duplicates
    RECORD_REGISTRY.clear()

    try:
        # Read and compile the file
        with open(file_path, encoding="utf-8") as f:
            content = f.read()

        # Compile and execute the code
        compiled = compile(content, str(file_path), "exec")

        # Create a namespace with the necessary imports
        namespace = {
            "record": record,
            "FlowRecord": FlowRecord,
            "__file__": str(file_path),
            "__name__": "__main__",
        }

        # Execute the code in the namespace
        exec(compiled, namespace)

        # Return only active flows (filter out reference-only FlowRecords
        # created for data access like record('type').data.field)
        flows = [f for f in RECORD_REGISTRY if f.is_active_flow()]
        for flow in flows:
            logger.info(f"Loaded flow: {flow.record_name}")

        return flows

    except Exception as e:
        logger.error(f"Error loading flows from {file_path}: {e}")
        import traceback

        traceback.print_exc()
        return []


def load_and_register_flows(engine: RecordFlowEngine, flow_files: list[Path]) -> int:
    """
    Load flows from multiple files and register them with the engine.

    Args:
        engine: RecordFlowEngine instance to register flows with.
        flow_files: List of paths to flow definition files.

    Returns:
        Total number of flows registered.
    """
    total_flows = 0

    for file_path in flow_files:
        flows = load_flows_from_file(file_path)
        for flow in flows:
            try:
                engine.register_flow(flow)
                total_flows += 1
            except Exception as e:
                logger.error(f"Error registering flow {flow.record_name}: {e}")

    logger.info(f"Registered {total_flows} flows from {len(flow_files)} files")
    return total_flows


def find_flow_files(base_path: Path, pattern: str = "*_flow.py") -> list[Path]:
    """
    Find flow definition files in a directory.

    Args:
        base_path: Base directory to search in.
        pattern: Glob pattern for flow files (default: *_flow.py).

    Returns:
        List of paths to flow definition files.
    """
    flow_files: list[Path] = []

    if base_path.exists() and base_path.is_dir():
        flow_files = list(base_path.glob(pattern))
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
