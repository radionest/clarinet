"""Schemathesis property-based API tests.

Automatically generates requests from OpenAPI schema and validates:
- No 500 errors on any generated input
- Response bodies match declared response_model
- Status codes are documented in schema
- Content-type headers are correct
- Stateful CRUD chains (POST → GET → DELETE) work correctly

Run: make test-schema
"""

import asyncio

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.generation.stateful import run_state_machine_as_test
from sqlmodel import SQLModel

schema = schemathesis.pytest.from_fixture("api_schema")
stateful_schema = schemathesis.pytest.from_fixture("stateful_api_schema")

# Core API endpoints (no external service dependencies)
CORE_API_PATTERN = r"^/api/(records|patients|studies|series|user|admin|auth|pipelines|health)"

# Excluded: external services + fastapi-users auto-generated endpoints
EXCLUDED_PATTERN = (
    r"^/(api/dicom|api/slicer|dicom-web)"
    r"|^/api/auth/(login|logout|register)"
    r"|^/api/records/\{record_id\}/submit$"
    # Starlette routing bug: negative data with control chars (e.g. %0A) in UUID
    # path params causes route fallthrough (/{user_id} → /), returning list instead
    # of object. Covered by test_medium_endpoints::test_user_read_endpoints.
    r"|^/api/user/\{user_id\}"
)

# Suppress common health checks for ASGI transport
_SUPPRESS = [HealthCheck.too_slow, HealthCheck.filter_too_much]


# ---------------------------------------------------------------------------
# Phase 1: Conformance + server error tests (all core endpoints)
# ---------------------------------------------------------------------------


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
@pytest.mark.timeout(300)
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
@pytest.mark.timeout(300)
def test_no_server_errors(case):
    """Verify API never returns 500 on any generated input."""
    response = case.call()
    assert response.status_code < 500, (
        f"Server error on {case.method} {case.path}: {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Phase 2: Stateful testing (CRUD chains via OpenAPI links)
# ---------------------------------------------------------------------------


@pytest.mark.schema
@pytest.mark.timeout(300)
def test_api_stateful(stateful_api_schema, stateful_db_engine):
    """Test CRUD operation chains via stateful state machine.

    Uses OpenAPI links injected in conftest to chain operations:
    POST /records/types → GET /records/types/{id} → DELETE, etc.
    Verifies that create → read → update → delete sequences work correctly.

    Each Hypothesis example gets a clean DB (drop + recreate all tables)
    to prevent UNIQUE constraint violations from leaking between examples,
    which causes FlakyStrategyDefinition errors during shrinking.
    """
    engine, superuser = stateful_db_engine

    base_state_machine = (
        stateful_api_schema.include(
            path_regex=CORE_API_PATTERN,
        )
        .exclude(
            path_regex=EXCLUDED_PATTERN,
        )
        .as_state_machine()
    )

    class CleanDBStateMachine(base_state_machine):
        """State machine with DB cleanup between Hypothesis examples."""

        def teardown(self):
            """Reset DB after each example to prevent cross-example state leakage."""

            async def _reset_db():
                async with engine.begin() as conn:
                    await conn.run_sync(SQLModel.metadata.drop_all)
                    await conn.run_sync(SQLModel.metadata.create_all)
                # Re-create the superuser (needed for auth overrides)
                from sqlalchemy.ext.asyncio import AsyncSession
                from sqlalchemy.orm import sessionmaker

                factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                async with factory() as session:
                    from clarinet.models.user import User

                    user = User(
                        id=superuser.id,
                        email=superuser.email,
                        hashed_password=superuser.hashed_password,
                        is_active=True,
                        is_verified=True,
                        is_superuser=True,
                    )
                    session.add(user)
                    await session.commit()

            asyncio.run(_reset_db())

    run_state_machine_as_test(
        CleanDBStateMachine,
        settings=settings(
            max_examples=50,
            stateful_step_count=4,
            suppress_health_check=_SUPPRESS,
            deadline=None,
        ),
    )
