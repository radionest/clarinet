"""Integration tests for ``PATCH /api/records/{id}/context-info``.

Covers the markdown→HTML pipeline (rendering + sanitisation), input validation,
and the three-way RBAC contract (superuser / owner / non-owner).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.api.app import app
from clarinet.api.auth_config import current_active_user, current_superuser
from clarinet.models.record import RecordType
from clarinet.models.user import User, UserRole, UserRolesLink
from clarinet.utils.auth import get_password_hash
from tests.conftest import setup_auth_overrides
from tests.utils.factories import (
    make_patient,
    make_series,
    make_study,
    seed_record,
)
from tests.utils.urls import RECORDS_BASE


@pytest_asyncio.fixture
async def env(test_session):
    """Patient + study + series + role-restricted RecordType + record."""
    pat = make_patient("CTX_PAT_001", "Ctx Patient")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("CTX_PAT_001", "1.2.3.700")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.700", "1.2.3.700.1", 1)
    test_session.add(series)
    await test_session.commit()

    test_session.add(UserRole(name="ctx-tester"))
    await test_session.commit()

    rt = RecordType(name="ctx-rt", level="SERIES", role_name="ctx-tester")
    test_session.add(rt)
    await test_session.commit()

    record = await seed_record(
        test_session,
        patient_id="CTX_PAT_001",
        study_uid="1.2.3.700",
        series_uid="1.2.3.700.1",
        rt_name="ctx-rt",
    )
    return {"record": record, "rt": rt}


async def _make_user(test_session, *, role: str | None) -> User:
    """Create a non-superuser, optionally linked to a role.

    The returned user is detached but has ``roles`` eagerly loaded — required
    by ``get_user_role_names`` once the request handler reads it without a
    live session.
    """
    user = User(
        id=uuid4(),
        email=f"u-{uuid4().hex[:6]}@test.com",
        hashed_password=get_password_hash("x"),
        is_active=True,
        is_verified=True,
        is_superuser=False,
    )
    test_session.add(user)
    await test_session.commit()
    if role is not None:
        test_session.add(UserRolesLink(user_id=user.id, role_name=role))
        await test_session.commit()

    result = await test_session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    loaded = result.scalar_one()
    test_session.expunge(loaded)
    return loaded


# === Functional tests (default `client` is superuser) =====================


@pytest.mark.asyncio
async def test_renders_markdown(client, env):
    """Markdown source is preserved; HTML field renders inline formatting."""
    rid = env["record"].id

    resp = await client.patch(
        f"{RECORDS_BASE}/{rid}/context-info",
        json={"context_info": "**bold** _italic_"},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["context_info"] == "**bold** _italic_"
    assert "<strong>bold</strong>" in payload["context_info_html"]
    assert "<em>italic</em>" in payload["context_info_html"]


@pytest.mark.asyncio
async def test_strips_script_tag(client, env):
    """Inline ``<script>`` from the source is removed from the rendered HTML."""
    rid = env["record"].id

    resp = await client.patch(
        f"{RECORDS_BASE}/{rid}/context-info",
        json={"context_info": "Hi <script>alert(1)</script> there"},
    )

    assert resp.status_code == 200
    rendered = resp.json()["context_info_html"]
    assert "<script>" not in rendered
    assert "alert" not in rendered
    assert "Hi" in rendered and "there" in rendered


@pytest.mark.asyncio
async def test_clears_with_null(client, env):
    """``null`` resets ``context_info`` and ``context_info_html`` together."""
    rid = env["record"].id

    await client.patch(f"{RECORDS_BASE}/{rid}/context-info", json={"context_info": "abc"})
    resp = await client.patch(f"{RECORDS_BASE}/{rid}/context-info", json={"context_info": None})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["context_info"] is None
    assert payload["context_info_html"] is None


@pytest.mark.asyncio
async def test_renders_table(client, env):
    """The ``tables`` Markdown extension is enabled in the renderer."""
    rid = env["record"].id
    md = "| col1 | col2 |\n|------|------|\n| a | b |"

    resp = await client.patch(f"{RECORDS_BASE}/{rid}/context-info", json={"context_info": md})

    assert resp.status_code == 200
    rendered = resp.json()["context_info_html"]
    assert "<table>" in rendered
    assert "<th>col1</th>" in rendered


@pytest.mark.asyncio
async def test_max_length_rejected(client, env):
    """Input longer than 3000 chars is rejected with 422."""
    rid = env["record"].id

    resp = await client.patch(
        f"{RECORDS_BASE}/{rid}/context-info",
        json={"context_info": "x" * 3001},
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_record_not_found(client):
    """Missing record id surfaces as 404."""
    resp = await client.patch(
        f"{RECORDS_BASE}/99999/context-info",
        json={"context_info": "x"},
    )

    assert resp.status_code == 404


# === Permission tests =====================================================


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(unauthenticated_client, env):
    """No session cookie + no DI override → fastapi-users returns 401."""
    app.dependency_overrides.pop(current_active_user, None)
    app.dependency_overrides.pop(current_superuser, None)

    try:
        rid = env["record"].id
        resp = await unauthenticated_client.patch(
            f"{RECORDS_BASE}/{rid}/context-info",
            json={"context_info": "x"},
        )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(current_active_user, None)
        app.dependency_overrides.pop(current_superuser, None)


@pytest.mark.asyncio
async def test_javascript_url_in_link_stripped(client, env):
    """``[text](javascript:...)`` is filtered by the URL-scheme whitelist."""
    rid = env["record"].id

    resp = await client.patch(
        f"{RECORDS_BASE}/{rid}/context-info",
        json={"context_info": "[click](javascript:alert(1))"},
    )

    assert resp.status_code == 200
    rendered = resp.json()["context_info_html"]
    assert "javascript:" not in rendered
    assert 'href="javascript' not in rendered


@pytest.mark.asyncio
async def test_owner_can_update(test_session, test_settings, env):
    """Non-superuser whose id matches record.user_id is allowed."""
    user = await _make_user(test_session, role="ctx-tester")
    record = env["record"]
    record.user_id = user.id
    test_session.add(record)
    await test_session.commit()

    setup_auth_overrides(user, test_session, test_settings)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"{RECORDS_BASE}/{record.id}/context-info",
                json={"context_info": "owner update"},
            )
        assert resp.status_code == 200
        assert resp.json()["context_info"] == "owner update"
    finally:
        app.dependency_overrides.pop(current_active_user, None)
        app.dependency_overrides.pop(current_superuser, None)


@pytest.mark.asyncio
async def test_role_user_on_unassigned_record_allowed(test_session, test_settings, env):
    """Role-authorised user can update a record with ``user_id is None``."""
    user = await _make_user(test_session, role="ctx-tester")

    record = env["record"]
    assert record.user_id is None  # seeded without an owner
    setup_auth_overrides(user, test_session, test_settings)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"{RECORDS_BASE}/{record.id}/context-info",
                json={"context_info": "claimed"},
            )
        assert resp.status_code == 200
        assert resp.json()["context_info"] == "claimed"
    finally:
        app.dependency_overrides.pop(current_active_user, None)
        app.dependency_overrides.pop(current_superuser, None)


@pytest.mark.asyncio
async def test_non_owner_with_role_match_forbidden(test_session, test_settings, env):
    """Role match alone is not enough — non-owner gets 403 from MutableRecordDep."""
    owner = await _make_user(test_session, role=None)
    other = await _make_user(test_session, role="ctx-tester")

    record = env["record"]
    record.user_id = owner.id
    test_session.add(record)
    await test_session.commit()

    setup_auth_overrides(other, test_session, test_settings)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.patch(
                f"{RECORDS_BASE}/{record.id}/context-info",
                json={"context_info": "should fail"},
            )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(current_active_user, None)
        app.dependency_overrides.pop(current_superuser, None)
