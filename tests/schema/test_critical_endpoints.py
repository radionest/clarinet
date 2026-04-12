"""Phase 3: Per-endpoint Schemathesis tests for critical endpoints.

Thorough property-based testing with max_examples=200 for endpoints
with the most complex validation, business rules, and edge case potential.

Run: make test-schema
"""

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import positive_data_acceptance

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
    case.call_and_validate(excluded_checks=[positive_data_acceptance])


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
    case.call_and_validate(excluded_checks=[positive_data_acceptance])


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

    Targets: RecordSearchQuery body, RecordFindResult computed sql_type,
    comparison_operator enum, sentinel values ("Null", "*"),
    cursor-based keyset pagination.

    Excludes positive_data_acceptance: cursor field is an opaque base64-encoded
    token whose validity cannot be expressed in OpenAPI schema (any string
    matching the charset can still fail JSON/structure decoding).
    """
    case.call_and_validate(excluded_checks=[positive_data_acceptance])


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


# ---------------------------------------------------------------------------
# Record status transitions
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/{record_id}/status", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_update_record_status(case):
    """Thorough testing of record status transitions.

    Targets: RecordStatus enum validation, state machine transitions,
    RecordFlow triggers on status change, AuthorizedRecordDep RBAC,
    timestamp auto-update (started_at/finished_at via event listener).
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Record user assignment
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/{record_id}/user", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_assign_record_user(case):
    """Thorough testing of record user assignment.

    Targets: UUID query param validation, AuthorizedRecordDep RBAC,
    RecordFlow triggers on user change, unique_per_user constraint check.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Bulk status update
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/bulk/status", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_bulk_update_record_status(case):
    """Thorough testing of bulk record status update.

    Targets: list[int] body with ge=1/le=2147483647 constraints,
    RecordStatus enum, RBAC role check per record, 204 no-content response,
    empty list edge case.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Record partial update
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/{record_id}", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_update_record(case):
    """Thorough testing of record partial update.

    Targets: RecordOptional body (viewer_study_uids, viewer_series_uids),
    exclude_unset logic (empty body = no-op), path param ge=1/le=2147483647,
    list[str] | None coercion.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/{record_id}/check-files", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_check_record_files(case):
    """Thorough testing of file checksum check endpoint.

    Targets: auto-unblock logic (blocked -> pending), checksum comparison,
    file_registry pattern resolution, FileCheckResult response model,
    empty file_links edge case, parent record loading.
    """
    case.call_and_validate()


@(schema.include(path="/api/records/{record_id}/validate-files", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_validate_record_files(case):
    """Thorough testing of file validation endpoint.

    Targets: FileValidationResult response model, parent record loading,
    file_registry resolution, records with no file_registry (returns valid=True).
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# RecordType search and delete
# ---------------------------------------------------------------------------


@(schema.include(path="/api/records/types/find", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_find_record_types(case):
    """Thorough testing of record type search.

    Targets: RecordTypeFind body (name, constraint_role), all-optional fields,
    partial match semantics, empty result set.
    """
    case.call_and_validate()


@(schema.include(path="/api/records/types/{record_type_id}", method="DELETE").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_delete_record_type(case):
    """Thorough testing of record type deletion.

    Targets: cascade delete (records referencing this type), 204 no-content,
    require_mutable_config guard, TOML file cleanup in background task,
    non-existent ID (404).
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Series search
# ---------------------------------------------------------------------------


@(schema.include(path="/api/series/find", method="POST").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_find_series(case):
    """Thorough testing of series search.

    Targets: SeriesFind body (nested records list with RecordFind),
    all-optional fields, study_uid FK filter, modality/instance_count types.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Admin record management
# ---------------------------------------------------------------------------


@(schema.include(path="/api/admin/records/{record_id}/assign", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_admin_assign_record(case):
    """Thorough testing of admin record assignment.

    Targets: path param ge=1/le=2147483647, UUID query param,
    superuser-only access, RecordFlow triggers.
    """
    case.call_and_validate()


@(schema.include(path="/api/admin/records/{record_id}/status", method="PATCH").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_admin_update_record_status(case):
    """Thorough testing of admin record status update.

    Targets: superuser bypass of state machine guards,
    path param constraints, RecordStatus enum, RecordFlow triggers.
    """
    case.call_and_validate()


@(schema.include(path="/api/admin/records/{record_id}/user", method="DELETE").parametrize())
@settings(
    max_examples=200,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_admin_unassign_record_user(case):
    """Thorough testing of admin record user unassignment.

    Targets: inwork -> pending status reset, path param constraints,
    RecordFlow triggers, record without user (no-op edge case).
    """
    case.call_and_validate()
