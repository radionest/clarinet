"""Schemathesis property-based API tests.

Automatically generates requests from OpenAPI schema and validates:
- No 500 errors on any generated input
- Response bodies match declared response_model
- Status codes are documented in schema
- Content-type headers are correct

Run: make test-schema
"""

import pytest
import schemathesis
from hypothesis import HealthCheck, settings

schema = schemathesis.pytest.from_fixture("api_schema")

# Core API endpoints (no external service dependencies)
CORE_API_PATTERN = r"^/api/(records|patients|studies|series|user|admin|auth|pipelines|health)"

# Excluded: external services + fastapi-users auto-generated endpoints
EXCLUDED_PATTERN = (
    r"^/(api/dicom|api/slicer|dicom-web)"
    r"|^/api/auth/(login|logout|register)"
)

# Suppress common health checks for ASGI transport
_SUPPRESS = [HealthCheck.too_slow, HealthCheck.filter_too_much]


@(
    schema.include(path_regex=CORE_API_PATTERN)
    .exclude(
        path_regex=EXCLUDED_PATTERN,
    )
    .parametrize()
)
@settings(
    max_examples=10,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_api_conformance(case):
    """Validate API endpoints conform to their OpenAPI schema."""
    case.call_and_validate()


@(
    schema.include(path_regex=CORE_API_PATTERN)
    .exclude(
        path_regex=EXCLUDED_PATTERN,
    )
    .parametrize()
)
@settings(
    max_examples=10,
    suppress_health_check=_SUPPRESS,
    deadline=None,
)
@pytest.mark.schema
def test_no_server_errors(case):
    """Verify API never returns 500 on any generated input."""
    response = case.call()
    assert response.status_code < 500, (
        f"Server error on {case.method} {case.path}: {response.status_code}"
    )
