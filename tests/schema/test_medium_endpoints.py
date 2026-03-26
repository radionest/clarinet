"""Phase 1b: Medium-depth Schemathesis tests for moderate-complexity endpoints.

Endpoints tested here have moderate complexity -- list/pagination, RBAC filtering,
computed fields, read-with-relations -- and benefit from deeper coverage than
Phase 1 (max_examples=10) but don't need Phase 3 depth (max_examples=200).

Run: make test-schema
"""

import pytest
import schemathesis
from hypothesis import HealthCheck, settings

schema = schemathesis.pytest.from_fixture("api_schema")

# Suppress common health checks for ASGI transport
_SUPPRESS = [HealthCheck.too_slow, HealthCheck.filter_too_much]


# ---------------------------------------------------------------------------
# Records: list, filter, read endpoints
# ---------------------------------------------------------------------------


@(
    schema.include(
        path_regex=r"^/api/records/(my(/pending)?|available_types|\{record_id\}(/schema)?)?$",
        method="GET",
    ).parametrize()
)
@settings(
    max_examples=50,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_record_read_endpoints(case):
    """Medium-depth testing of record read/list/filter endpoints.

    Targets: RBAC filtering (GET /, /my, /my/pending), computed fields
    (available_types, working_folder), AuthorizedRecordDep on /{id},
    hydrated schema 200/204 responses on /{id}/schema.
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Record Types: list and get
# ---------------------------------------------------------------------------


@(
    schema.include(
        path_regex=r"^/api/records/types(/\{record_type_id\})?$",
        method="GET",
    ).parametrize()
)
@settings(
    max_examples=50,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_record_type_read_endpoints(case):
    """Medium-depth testing of record type list/get endpoints.

    Targets: GET /types (list all), GET /types/{id} (by ID, 404 on missing).
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Study/Series/Patient: read endpoints
# ---------------------------------------------------------------------------


@(
    schema.include(
        path_regex=(
            r"^/api/(patients(/\{patient_id\})?"
            r"|studies(/\{study_uid\}(/series)?)?"
            r"|series(/random|/\{series_uid\})?)$"
        ),
        method="GET",
    ).parametrize()
)
@settings(
    max_examples=50,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_study_read_endpoints(case):
    """Medium-depth testing of patient/study/series read endpoints.

    Targets: DicomUID path param validation, FK cascade reads,
    nested relations (StudyRead -> PatientBase, SeriesBase),
    GET /series/random (empty DB edge case).
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# User management: read + update endpoints
# ---------------------------------------------------------------------------


@(
    schema.include(
        path_regex=(
            r"^/api/user(/me(/roles)?"
            r"|/roles(/\{role_name\})?"
            r"|/\{user_id\}(/roles)?"
            r"|/?)$"
        ),
        method_regex=r"^(GET|PUT)$",
    ).parametrize()
)
@settings(
    max_examples=50,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_user_read_endpoints(case):
    """Medium-depth testing of user read/update endpoints.

    Targets: UUID path param validation, pagination params,
    role name constraints, PUT update (UserUpdate body).
    """
    case.call_and_validate()


# ---------------------------------------------------------------------------
# Admin: read endpoints (stats, matrix)
# ---------------------------------------------------------------------------


@(
    schema.include(
        path_regex=r"^/api/admin/(stats|role-matrix|record-types/stats)$",
        method="GET",
    ).parametrize()
)
@settings(
    max_examples=50,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_admin_read_endpoints(case):
    """Medium-depth testing of admin statistics endpoints.

    Targets: aggregate queries on empty DB, computed fields,
    nested response models (RoleMatrixResponse, RecordTypeStats).
    """
    case.call_and_validate()
