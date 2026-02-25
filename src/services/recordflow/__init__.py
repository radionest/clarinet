"""
RecordFlow - Event-driven workflow orchestration for Clarinet.

This module provides a DSL-based system for defining workflows that automatically
create or update records based on status changes and conditions.

Example usage:
    from src.services.recordflow import record, RecordFlowEngine

    # Define a flow
    record('doctor_report')
        .on_status('finished')
        .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
        .add_record('confirm_birads')

    # Create and configure the engine
    engine = RecordFlowEngine(clarinet_client)
    engine.register_flow(my_flow)

    # Handle status changes
    await engine.handle_record_status_change(record, old_status)
"""

from .engine import RecordFlowEngine
from .flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    FlowAction,
    InvalidateRecordsAction,
    UpdateRecordAction,
)
from .flow_builder import flow, patient, record, series, study
from .flow_condition import FlowCondition
from .flow_loader import (
    discover_and_load_flows,
    find_flow_files,
    load_and_register_flows,
    load_flows_from_file,
)
from .flow_record import ENTITY_REGISTRY, RECORD_REGISTRY, FlowRecord
from .flow_result import (
    ComparisonResult,
    ConstantFlowResult,
    FieldComparison,
    FlowResult,
    LogicalComparison,
)

__all__ = [
    "ENTITY_REGISTRY",
    "RECORD_REGISTRY",
    "CallFunctionAction",
    "ComparisonResult",
    "ConstantFlowResult",
    "CreateRecordAction",
    "FieldComparison",
    "FlowAction",
    "FlowCondition",
    "FlowRecord",
    "FlowResult",
    "InvalidateRecordsAction",
    "LogicalComparison",
    "RecordFlowEngine",
    "UpdateRecordAction",
    "discover_and_load_flows",
    "find_flow_files",
    "flow",
    "load_and_register_flows",
    "load_flows_from_file",
    "patient",
    "record",
    "series",
    "study",
]
