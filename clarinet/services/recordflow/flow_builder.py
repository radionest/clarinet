"""
Helper functions for building flow definitions.

This module re-exports the record() function and provides additional
convenience functions for flow definition.
"""

from .flow_file import file
from .flow_record import FlowRecord, patient, record, series, study
from .flow_result import Field

__all__ = ["Field", "FlowRecord", "file", "flow", "patient", "record", "series", "study"]

# Alias for convenience - some users may prefer 'flow' over 'record'
flow = record
