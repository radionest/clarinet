"""
Helper functions for building flow definitions.

This module re-exports the record() function and provides additional
convenience functions for flow definition.
"""

from .flow_record import FlowRecord, patient, record, series, study

__all__ = ["FlowRecord", "flow", "patient", "record", "series", "study"]

# Alias for convenience - some users may prefer 'flow' over 'record'
flow = record
