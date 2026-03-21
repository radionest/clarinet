"""Phase 3: Per-endpoint Schemathesis tests for critical endpoints.

Thorough property-based testing with max_examples=200 for endpoints
with the most complex validation, business rules, and edge case potential.

Run: make test-schema
"""

import pytest
import schemathesis
from hypothesis import HealthCheck, settings

schema = schemathesis.pytest.from_fixture("api_schema")

# Suppress common health checks for ASGI transport
_SUPPRESS = [HealthCheck.too_slow, HealthCheck.filter_too_much]


# ---------------------------------------------------------------------------
# Record CRUD
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_create_record(case):
    """Thorough testing of record creation.

    Targets: level-UID consistency validator, record_type_name slug,
    DicomUID constraints, empty_to_none coercion, parent_record_id validation.
    """
    case.call_and_validate()


@(schema.include(path="/api/records/{record_id}/data", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_submit_record_data(case):
    """Thorough testing of record data submission.

    Targets: free-form JSON body, state machine guards (blocked/finished → 409),
    JSON Schema Draft 2020-12 validation, file validation.
    """
    case.call_and_validate()


@(schema.include(path="/api/records/{record_id}/data", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_update_record_data(case):
    """Thorough testing of record data update.

    Targets: inverse state machine guard (must be finished), JSON Schema
    validation, data-update RecordFlow trigger.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# RecordType CRUD
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/types", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_create_record_type(case):
    """Thorough testing of record type creation.

    Targets: nested data_schema (JSON Schema validation), slug name pattern,
    file_registry with identifier validation, slicer_script_args,
    parse_json_strings validator, require_mutable_config guard.
    """
    case.call_and_validate()


@(schema.include(path="/api/records/types/{record_type_id}", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_update_record_type(case):
    """Thorough testing of record type update.

    Targets: all-optional fields, parse_json_strings dual-mode parsing,
    model_fields_set vs exclude_unset logic, data_schema re-validation.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Record search
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/find", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_find_records(case):
    """Thorough testing of record search.

    Targets: mixed body + query params, RecordFindResult computed sql_type,
    comparison_operator enum, sentinel values ("Null", "*"),
    pagination edge cases (skip=-1, limit=0).
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Record invalidation
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/{record_id}/invalidate", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_invalidate_record(case):
    """Thorough testing of record invalidation.

    Targets: unvalidated mode string (only "hard"/"soft" handled),
    reason appended to context_info (max 3000 chars), optional body fields.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Series creation
# ---------------------------------------------------------------------------


@(schema.include(path="/api/series", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_create_series(case):
    """Thorough testing of series creation.

    Targets: DicomUID pattern constraints, series_number boundaries (gt=0, lt=100000),
    study_uid FK validation, anon_uid constraints, empty_to_none coercion.
    """
    case.call_and_validate()
