# Capability-based Access Control (reports first) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a project grant a non-admin role access to *only* the SQL + Quarto reports area by mapping roles to named capabilities in `settings.toml`.

**Architecture:** A thin "capability" layer over existing roles. Core owns a closed capability vocabulary (`{"reports"}`); `settings.role_capabilities` maps role → capabilities; admins/superusers implicitly hold all. Report endpoints swap their `AdminUserDep` guard for a `require_capability("reports")` dependency. The frontend receives each user's effective `capabilities` on the user object and gates nav/routing on them. No DB migration.

**Tech Stack:** Python 3.12 (FastAPI, SQLModel, pydantic-settings, pytest/pytest-asyncio), Gleam + Lustre frontend (gleeunit). All Python via `uv run`; frontend via `make frontend-check` / `gleam test` from `clarinet/frontend/`.

**Approved spec:** `docs/superpowers/specs/2026-06-26-access-capabilities-design.md`

## Global Constraints

- All Python commands run through `uv run` (or `make` targets). **First** `uv`/`make` call in this fresh worktree builds the venv — wrap it with `timeout 300`; later calls use `timeout 120`.
- Test output: redirect to `/tmp/test-access-capabilities.txt` (`> /tmp/test-access-capabilities.txt 2>&1`); never pipe to `tail`/`tee`.
- **Admins and superusers keep full access** to everything, including all capabilities — zero regression is mandatory.
- **No database migration** — capabilities live in settings, roles already exist as a table.
- Capability vocabulary is closed: the only known capability is `"reports"`. A role mapped to an unknown capability must fail startup (`ConfigurationError`).
- Conventional commit messages; no `Co-Authored-By` trailers.
- Env override for the new setting: `CLARINET_ROLE_CAPABILITIES` (JSON), consistent with other settings.
- Gleam: run all `gleam`/`make frontend-*` from `clarinet/frontend/`. Adding a field to a Gleam record breaks every constructor — the compiler enumerates missed sites; treat `make frontend-check` output as the completeness check.

---

## Task 1: Capability vocabulary, resolver, validation

**Files:**
- Create: `clarinet/models/capability.py`
- Test: `tests/test_capabilities.py`

**Interfaces:**
- Produces:
  - `Capability(StrEnum)` with `REPORTS = "reports"`
  - `KNOWN_CAPABILITIES: frozenset[str]`
  - `resolve_capabilities(role_names: Iterable[str], is_superuser: bool) -> list[str]` — sorted effective capabilities; superuser/`admin` → all known
  - `validate_role_capabilities(mapping: dict[str, list[str]]) -> None` — raises `ConfigurationError` on unknown capability
- Consumes: `settings.role_capabilities` (added in Task 2 — but Task 2's setting must exist for the resolver tests that monkeypatch it; **do Task 2's settings edit first, or implement Tasks 1+2 together**). See Task 2 note below.

> **Ordering note:** `resolve_capabilities` reads `settings.role_capabilities`. Apply the one-line settings change from **Task 2** before running Task 1's tests (or treat Tasks 1 and 2 as a single deliverable). The steps below assume `settings.role_capabilities` exists.

- [ ] **Step 1: Write the failing test** — `tests/test_capabilities.py`

```python
"""Unit tests for the capability vocabulary, resolver, and validation."""

import pytest

from clarinet.exceptions.domain import ConfigurationError
from clarinet.models.capability import (
    KNOWN_CAPABILITIES,
    Capability,
    resolve_capabilities,
    validate_role_capabilities,
)


def test_known_capabilities_contains_reports() -> None:
    assert Capability.REPORTS == "reports"
    assert "reports" in KNOWN_CAPABILITIES


def test_superuser_gets_all_capabilities() -> None:
    assert resolve_capabilities([], is_superuser=True) == sorted(KNOWN_CAPABILITIES)


def test_admin_role_gets_all_capabilities() -> None:
    assert resolve_capabilities(["admin"], is_superuser=False) == sorted(KNOWN_CAPABILITIES)


def test_mapped_role_gets_its_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["reports"]})
    assert resolve_capabilities(["analyst"], is_superuser=False) == ["reports"]


def test_unmapped_role_gets_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["reports"]})
    assert resolve_capabilities(["doctor"], is_superuser=False) == []


def test_validate_rejects_unknown_capability() -> None:
    with pytest.raises(ConfigurationError):
        validate_role_capabilities({"analyst": ["reprots"]})


def test_validate_accepts_known_capability() -> None:
    validate_role_capabilities({"analyst": ["reports"]})  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 300 uv run pytest tests/test_capabilities.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: FAIL — `ModuleNotFoundError: No module named 'clarinet.models.capability'`

- [ ] **Step 3: Write the implementation** — `clarinet/models/capability.py`

```python
"""Capability vocabulary and role→capability resolution.

A *capability* is a coarse-grained, named permission for a feature area
(initially: reports). Projects map roles to capabilities in
``settings.role_capabilities``; superusers and members of the built-in
``admin`` role implicitly hold every known capability. This decouples feature
access from the monolithic ``admin`` role without a DB-backed permission table.
"""

from collections.abc import Iterable
from enum import StrEnum

from clarinet.exceptions.domain import ConfigurationError
from clarinet.settings import settings


class Capability(StrEnum):
    """The closed vocabulary of capabilities a role may be granted."""

    REPORTS = "reports"


KNOWN_CAPABILITIES: frozenset[str] = frozenset(c.value for c in Capability)


def resolve_capabilities(role_names: Iterable[str], is_superuser: bool) -> list[str]:
    """Return the sorted effective capabilities for a user.

    Superusers and members of the built-in ``admin`` role implicitly hold every
    known capability. Everyone else gets the union of capabilities mapped to
    their roles via ``settings.role_capabilities``.
    """
    names = set(role_names)
    if is_superuser or "admin" in names:
        return sorted(KNOWN_CAPABILITIES)
    granted: set[str] = set()
    for role in names:
        granted.update(settings.role_capabilities.get(role, []))
    return sorted(granted)


def validate_role_capabilities(mapping: dict[str, list[str]]) -> None:
    """Fail-fast when the mapping references a capability outside the vocabulary.

    Mirrors the role/viewer reference checks in ``reconcile_config``: a typo like
    ``"reprots"`` should refuse startup, not silently deny access.
    """
    referenced: set[str] = set()
    for caps in mapping.values():
        referenced.update(caps)
    unknown = referenced - KNOWN_CAPABILITIES
    if unknown:
        raise ConfigurationError(
            f"settings.role_capabilities references unknown capability/ies: "
            f"{sorted(unknown)}.\nKnown capabilities: {sorted(KNOWN_CAPABILITIES)}."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_capabilities.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit** (combine with Task 2 — commit after Task 2 so the setting is included)

---

## Task 2: `role_capabilities` setting

**Files:**
- Modify: `clarinet/settings.py:261-262` (the "Role settings" block)

**Interfaces:**
- Produces: `settings.role_capabilities: dict[str, list[str]]` (default `{}`)
- Consumed by: `resolve_capabilities` (Task 1), `validate_role_capabilities` (Task 5)

- [ ] **Step 1: Add the setting** — edit `clarinet/settings.py`

Replace:
```python
    # Role settings
    extra_roles: list[str] = []
```
with:
```python
    # Role settings
    extra_roles: list[str] = []
    # Maps a role name to the capabilities it grants (e.g. {"analyst": ["reports"]}).
    # Roles named here are auto-created at startup; capability values are
    # validated against the known vocabulary (clarinet/models/capability.py).
    # Env override: CLARINET_ROLE_CAPABILITIES as JSON.
    role_capabilities: dict[str, list[str]] = {}
```

- [ ] **Step 2: Verify it loads**

Run: `timeout 120 uv run python -c "from clarinet.settings import settings; print(settings.role_capabilities)" > /tmp/test-access-capabilities.txt 2>&1`
Expected: prints `{}` with no error

- [ ] **Step 3: Re-run Task 1 tests (now green) and commit**

Run: `timeout 120 uv run pytest tests/test_capabilities.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS (7 passed)

```bash
git add clarinet/models/capability.py clarinet/settings.py tests/test_capabilities.py
git commit -m "feat(auth): add capability vocabulary, resolver, and role_capabilities setting"
```

---

## Task 3: `capabilities` on `User` and `UserRead`

**Files:**
- Modify: `clarinet/models/user.py:64-85`
- Test: `tests/test_user_capabilities.py`

**Interfaces:**
- Consumes: `resolve_capabilities` (Task 1)
- Produces: `User.capabilities` (computed property, list[str]) and `UserRead.capabilities` (serialized field). Every endpoint with `response_model=UserRead` now emits `capabilities`.

- [ ] **Step 1: Write the failing test** — `tests/test_user_capabilities.py`

```python
"""The User computed field and UserRead expose effective capabilities."""

import pytest

from clarinet.models.user import User, UserRead, UserRole


def _user(is_superuser: bool, role_names: list[str]) -> User:
    user = User(email="cap@test.co", hashed_password="x", is_superuser=is_superuser)
    # role_names reads __dict__["roles"] directly (see User.role_names); set it
    # the same way the auth flow's eager-load would.
    user.__dict__["roles"] = [UserRole(name=n) for n in role_names]
    return user


def test_user_capabilities_from_mapped_role(monkeypatch: pytest.MonkeyPatch) -> None:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["reports"]})
    assert _user(False, ["analyst"]).capabilities == ["reports"]


def test_user_capabilities_superuser_has_reports() -> None:
    assert "reports" in _user(True, []).capabilities


def test_userread_serializes_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["reports"]})
    read = UserRead.model_validate(_user(False, ["analyst"]))
    assert read.capabilities == ["reports"]
    assert read.role_names == ["analyst"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 uv run pytest tests/test_user_capabilities.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'capabilities'` (and `UserRead` has no `capabilities`)

- [ ] **Step 3: Add the computed field on `User`** — insert after the `role_names` property (after line 79, before the closing of the class / before `class UserRead`)

```python
    @computed_field  # type: ignore[prop-decorator]
    @property
    def capabilities(self) -> list[str]:
        # Effective capabilities derived from roles + settings mapping.
        # Local import avoids any models/__init__ import-order coupling.
        from clarinet.models.capability import resolve_capabilities

        return resolve_capabilities(self.role_names, self.is_superuser)
```

- [ ] **Step 4: Add the field on `UserRead`** — in `class UserRead`, right after the `role_names` field (line 85)

```python
    role_names: list[str] = PydanticField(default_factory=list)
    capabilities: list[str] = PydanticField(default_factory=list)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_user_capabilities.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add clarinet/models/user.py tests/test_user_capabilities.py
git commit -m "feat(auth): expose effective capabilities on User and UserRead"
```

---

## Task 4: `require_capability` dependency + `ReportsAccessDep`

**Files:**
- Modify: `clarinet/api/dependencies.py` (imports near line 20; new code after `AdminUserDep` at line 485)
- Test: `tests/test_require_capability.py`

**Interfaces:**
- Consumes: `Capability`, `resolve_capabilities` (Task 1); `CurrentUserDep` (line 52)
- Produces:
  - `require_capability(capability: str) -> Callable[[User], Awaitable[User]]`
  - `ReportsAccessDep = Annotated[User, Depends(require_capability(Capability.REPORTS))]`

- [ ] **Step 1: Write the failing test** — `tests/test_require_capability.py`

```python
"""The require_capability dependency allows holders and 403s everyone else."""

import pytest
from fastapi import HTTPException

from clarinet.api.dependencies import require_capability
from clarinet.models.user import User, UserRole


def _user(is_superuser: bool, role_names: list[str]) -> User:
    user = User(email="dep@test.co", hashed_password="x", is_superuser=is_superuser)
    user.__dict__["roles"] = [UserRole(name=n) for n in role_names]
    return user


@pytest.mark.asyncio
async def test_allows_capability_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["reports"]})
    dep = require_capability("reports")
    user = _user(False, ["analyst"])
    assert await dep(user) is user


@pytest.mark.asyncio
async def test_allows_superuser() -> None:
    dep = require_capability("reports")
    user = _user(True, [])
    assert await dep(user) is user


@pytest.mark.asyncio
async def test_denies_non_holder(monkeypatch: pytest.MonkeyPatch) -> None:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {})
    dep = require_capability("reports")
    with pytest.raises(HTTPException) as exc:
        await dep(_user(False, ["doctor"]))
    assert exc.value.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 uv run pytest tests/test_require_capability.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: FAIL — `ImportError: cannot import name 'require_capability'`

- [ ] **Step 3: Add imports** — `clarinet/api/dependencies.py`, near the top. After line 5 (`from typing import Annotated`) ensure `Awaitable, Callable` are importable; add:

```python
from collections.abc import Awaitable, Callable
```

and after line 20 (`from clarinet.models import Record, User`) add:

```python
from clarinet.models.capability import Capability, resolve_capabilities
```

- [ ] **Step 4: Add the factory + alias** — after `AdminUserDep = Annotated[User, Depends(current_admin_user)]` (line 485)

```python
def require_capability(capability: str) -> Callable[[User], Awaitable[User]]:
    """Build a dependency that admits a user holding ``capability``.

    Superusers and the ``admin`` role implicitly hold every capability (see
    ``resolve_capabilities``), so this is a strict generalization of
    ``current_admin_user`` scoped to a single feature area.
    """

    async def _require(user: CurrentUserDep) -> User:
        if capability in resolve_capabilities(user.role_names, user.is_superuser):
            return user
        raise HTTPException(
            status_code=403,
            detail=f"Missing required capability: {capability}",
        )

    return _require


ReportsAccessDep = Annotated[User, Depends(require_capability(Capability.REPORTS))]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_require_capability.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add clarinet/api/dependencies.py tests/test_require_capability.py
git commit -m "feat(auth): add require_capability dependency and ReportsAccessDep"
```

---

## Task 5: Swap report guards + behavioural integration tests

**Files:**
- Modify: `clarinet/api/routers/reports.py` (lines 9, 36, 50)
- Modify: `clarinet/api/routers/quarto_reports.py` (lines 6, 28, 43, 58, 69)
- Modify: `tests/conftest.py` (add `create_mock_user_with_role` helper near `create_mock_superuser`, line 280)
- Modify: `tests/integration/test_reports.py` (new fixtures + tests)
- Modify: `tests/integration/test_quarto_reports.py` (new fixture + test)

**Interfaces:**
- Consumes: `ReportsAccessDep` (Task 4); `User.capabilities`/role loading (Task 3)
- Produces: `create_mock_user_with_role(session, role_name, email=, is_superuser=False) -> User`

- [ ] **Step 1: Write the failing tests — conftest helper** (`tests/conftest.py`, after `create_mock_superuser`, ~line 280)

```python
async def create_mock_user_with_role(
    session: AsyncSession,
    role_name: str,
    email: str = "roled@test.com",
    is_superuser: bool = False,
) -> User:
    """Create a user with one role, roles eagerly loaded then detached.

    Mirrors ``create_mock_superuser`` but attaches a ``UserRole`` so
    ``role_names`` / ``capabilities`` resolve in capability-gated endpoint tests.
    """
    from sqlalchemy.orm import selectinload
    from sqlmodel import select

    from clarinet.models.user import User, UserRole, UserRolesLink
    from clarinet.utils.auth import get_password_hash

    if await session.get(UserRole, role_name) is None:
        session.add(UserRole(name=role_name))
        await session.commit()

    user = User(
        id=uuid4(),
        email=email,
        hashed_password=get_password_hash("mock"),
        is_active=True,
        is_verified=True,
        is_superuser=is_superuser,
    )
    session.add(user)
    await session.commit()

    session.add(UserRolesLink(user_id=user.id, role_name=role_name))
    await session.commit()
    session.expire_all()

    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    loaded = result.scalar_one()
    session.expunge(loaded)
    return loaded
```

- [ ] **Step 2: Write the failing tests — SQL reports** (`tests/integration/test_reports.py`)

Update the import line 15:
```python
from tests.conftest import (
    create_authenticated_client,
    create_mock_superuser,
    create_mock_user_with_role,
)
```
Update the urls import (line 16):
```python
from tests.utils.urls import ADMIN_REPORTS, ADMIN_STATS, AUTH_ME
```
Append these fixtures + tests at the end of the file:
```python
@pytest_asyncio.fixture
async def analyst_report_client(
    test_session, test_settings, monkeypatch
) -> AsyncGenerator[AsyncClient]:
    """Non-admin 'analyst' client; analyst role maps to the reports capability."""
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["reports"]})
    user = await create_mock_user_with_role(
        test_session, "analyst", email="analyst@test.com"
    )
    app.dependency_overrides[get_report_registry] = lambda: _make_registry()
    async for ac in create_authenticated_client(user, test_session, test_settings):
        yield ac
    app.dependency_overrides.pop(get_report_registry, None)


@pytest_asyncio.fixture
async def plain_report_client(
    test_session, test_settings, monkeypatch
) -> AsyncGenerator[AsyncClient]:
    """Non-admin user with no capability mapping — must be denied reports."""
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {})
    user = await create_mock_user_with_role(
        test_session, "doctor", email="plain@test.com"
    )
    app.dependency_overrides[get_report_registry] = lambda: _make_registry()
    async for ac in create_authenticated_client(user, test_session, test_settings):
        yield ac
    app.dependency_overrides.pop(get_report_registry, None)


async def test_analyst_can_list_reports(analyst_report_client: AsyncClient) -> None:
    resp = await analyst_report_client.get(ADMIN_REPORTS)
    assert resp.status_code == 200


async def test_analyst_can_download_report(analyst_report_client: AsyncClient) -> None:
    resp = await analyst_report_client.get(f"{ADMIN_REPORTS}/constant_one/download")
    assert resp.status_code == 200


async def test_analyst_denied_other_admin_endpoint(
    analyst_report_client: AsyncClient,
) -> None:
    resp = await analyst_report_client.get(ADMIN_STATS)
    assert resp.status_code == 403


async def test_analyst_me_includes_reports_capability(
    analyst_report_client: AsyncClient,
) -> None:
    resp = await analyst_report_client.get(AUTH_ME)
    assert resp.status_code == 200
    assert resp.json()["capabilities"] == ["reports"]


async def test_plain_user_denied_reports(plain_report_client: AsyncClient) -> None:
    resp = await plain_report_client.get(ADMIN_REPORTS)
    assert resp.status_code == 403
```

- [ ] **Step 3: Write the failing test — Quarto reports** (`tests/integration/test_quarto_reports.py`)

Update the conftest import (line 14) to add `create_mock_user_with_role`, and append:
```python
@pytest_asyncio.fixture
async def analyst_quarto_client(
    test_session, test_settings, monkeypatch
) -> AsyncGenerator[AsyncClient]:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["reports"]})
    user = await create_mock_user_with_role(
        test_session, "analyst", email="analyst-q@test.com"
    )
    app.dependency_overrides[get_quarto_report_registry] = _make_registry
    async for ac in create_authenticated_client(user, test_session, test_settings):
        yield ac
    app.dependency_overrides.pop(get_quarto_report_registry, None)


async def test_analyst_can_list_quarto_reports(
    analyst_quarto_client: AsyncClient,
) -> None:
    resp = await analyst_quarto_client.get(ADMIN_QUARTO_REPORTS)
    assert resp.status_code == 200
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `timeout 120 uv run pytest tests/integration/test_reports.py tests/integration/test_quarto_reports.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: FAIL — analyst gets 403 on reports (guard still `AdminUserDep`); `AUTH_ME`/`ADMIN_STATS` import errors if not yet present in `tests/utils/urls.py` (AUTH_ME exists at line 18; ADMIN_STATS at line 67 — both already defined).

- [ ] **Step 5: Swap the guard — SQL reports** (`clarinet/api/routers/reports.py`)

Line 9, change the import:
```python
from clarinet.api.dependencies import ReportsAccessDep, ReportServiceDep
```
Line 36 (`list_reports`) and line 50 (`download_report`): replace the parameter type
```python
    _current_user: AdminUserDep,
```
with
```python
    _current_user: ReportsAccessDep,
```

- [ ] **Step 6: Swap the guard — Quarto reports** (`clarinet/api/routers/quarto_reports.py`)

Line 6, change the import:
```python
from clarinet.api.dependencies import QuartoReportServiceDep, ReportsAccessDep
```
Lines 28, 43, 58, 69 — replace each `_current_user: AdminUserDep,` with `_current_user: ReportsAccessDep,`.

- [ ] **Step 7: Run tests to verify they pass** (includes the existing superuser tests as regression)

Run: `timeout 120 uv run pytest tests/integration/test_reports.py tests/integration/test_quarto_reports.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS (all existing + new tests)

- [ ] **Step 8: Commit**

```bash
git add clarinet/api/routers/reports.py clarinet/api/routers/quarto_reports.py \
        tests/conftest.py tests/integration/test_reports.py tests/integration/test_quarto_reports.py
git commit -m "feat(reports): gate SQL+Quarto reports on the reports capability"
```

---

## Task 6: Startup validation + auto-create capability roles

**Files:**
- Modify: `clarinet/utils/bootstrap.py:34-37` (`add_default_user_roles`)
- Test: `tests/test_bootstrap_capabilities.py`

**Interfaces:**
- Consumes: `validate_role_capabilities` (Task 1), `settings.role_capabilities` (Task 2)

- [ ] **Step 1: Write the failing test** — `tests/test_bootstrap_capabilities.py`

```python
"""add_default_user_roles validates role_capabilities before touching the DB."""

import pytest

from clarinet.exceptions.domain import ConfigurationError
from clarinet.utils.bootstrap import add_default_user_roles


@pytest.mark.asyncio
async def test_rejects_unknown_capability(monkeypatch: pytest.MonkeyPatch) -> None:
    from clarinet.settings import settings

    monkeypatch.setattr(settings, "role_capabilities", {"analyst": ["bogus"]})
    with pytest.raises(ConfigurationError):
        await add_default_user_roles()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 uv run pytest tests/test_bootstrap_capabilities.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: FAIL — no `ConfigurationError` raised (validation not wired yet; the call instead tries the DB)

- [ ] **Step 3: Wire validation + role creation** — `clarinet/utils/bootstrap.py`

Replace:
```python
    from clarinet.settings import settings

    default_roles = ["doctor", "auto", "admin", "expert", "ordinator"]
    all_roles = list(dict.fromkeys(default_roles + settings.extra_roles))
```
with:
```python
    from clarinet.models.capability import validate_role_capabilities
    from clarinet.settings import settings

    # Fail fast on a typo'd capability before creating roles or hitting the DB.
    validate_role_capabilities(settings.role_capabilities)

    default_roles = ["doctor", "auto", "admin", "expert", "ordinator"]
    all_roles = list(
        dict.fromkeys(
            default_roles + settings.extra_roles + list(settings.role_capabilities)
        )
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 uv run pytest tests/test_bootstrap_capabilities.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add clarinet/utils/bootstrap.py tests/test_bootstrap_capabilities.py
git commit -m "feat(auth): validate role_capabilities and auto-create mapped roles at startup"
```

---

## Task 7: Frontend — `capabilities` on the User model + decoder

**Files:**
- Modify: `clarinet/frontend/src/api/models.gleam:143-154` (`User` type)
- Modify: `clarinet/frontend/src/api/users.gleam:22-42` (`user_decoder`)
- Modify: `clarinet/frontend/test/utils/record_filters_test.gleam:58` (`models.User(...)` constructor)
- Modify: `clarinet/frontend/test/previous_route_test.gleam:15` (`models.User(...)` constructor)

**Interfaces:**
- Produces: `User.capabilities: List(String)` on the frontend user record, decoded from the `capabilities` JSON field.

> Adding a field to the `User` record breaks every full constructor. Known sites: `user_decoder` + the two test files above. `models.User(id: uid, ..)` patterns in `cache.gleam:241-242` use `..` and need **no** change. `make frontend-check` will name any site you miss.

- [ ] **Step 1: Add the field to the `User` type** — `models.gleam`

```gleam
pub type User {
  User(
    id: String,
    // UUID Primary key
    email: String,
    // Unique email used for identification
    is_active: Bool,
    is_superuser: Bool,
    is_verified: Bool,
    role_names: List(String),
    capabilities: List(String),
  )
}
```

- [ ] **Step 2: Decode it in `user_decoder`** — `users.gleam`

After the `role_names` decode block (line 28-32), add:
```gleam
  use capabilities <- decode.optional_field(
    "capabilities",
    [],
    decode.list(decode.string),
  )
```
And add the field to the constructor:
```gleam
  decode.success(models.User(
    id: id,
    email: email,
    is_active: is_active,
    is_superuser: is_superuser,
    is_verified: is_verified,
    role_names: role_names,
    capabilities: capabilities,
  ))
```

- [ ] **Step 3: Fix the test constructors**

In `clarinet/frontend/test/utils/record_filters_test.gleam` (the `models.User(...)` at line 58) and `clarinet/frontend/test/previous_route_test.gleam` (line 15), add `capabilities: []` to the `models.User(...)` constructor.

- [ ] **Step 4: Type-check** (run from `clarinet/frontend/`)

Run: `timeout 300 make frontend-check > /tmp/test-access-capabilities.txt 2>&1`
Expected: 0 errors. If it reports `This constructor is missing field capabilities` in another file, add `capabilities: []` there and re-run.

- [ ] **Step 5: Run frontend unit tests**

Run (from `clarinet/frontend/`): `timeout 120 gleam test > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add clarinet/frontend/src/api/models.gleam clarinet/frontend/src/api/users.gleam \
        clarinet/frontend/test/utils/record_filters_test.gleam \
        clarinet/frontend/test/previous_route_test.gleam
git commit -m "feat(frontend): carry effective capabilities on the User model"
```

---

## Task 8: Frontend — `has_capability` helper + capability routing

**Files:**
- Modify: `clarinet/frontend/src/utils/permissions.gleam` (add `has_capability`)
- Modify: `clarinet/frontend/src/router.gleam:118-138` (`requires_admin_role`) + add `requires_capability`
- Test: `clarinet/frontend/test/capabilities_test.gleam`

**Interfaces:**
- Consumes: `User.capabilities` (Task 7)
- Produces: `permissions.has_capability(User, String) -> Bool`; `router.requires_capability(Route) -> Option(String)`; reports routes removed from `requires_admin_role`.

- [ ] **Step 1: Write the failing test** — `clarinet/frontend/test/capabilities_test.gleam`

```gleam
import api/models
import gleam/option.{None, Some}
import gleeunit/should
import router
import utils/permissions

fn make_user(
  role_names: List(String),
  capabilities: List(String),
  is_superuser: Bool,
) -> models.User {
  models.User(
    id: "u1",
    email: "u@test",
    is_active: True,
    is_superuser: is_superuser,
    is_verified: True,
    role_names: role_names,
    capabilities: capabilities,
  )
}

pub fn has_capability_true_when_listed_test() {
  make_user([], ["reports"], False)
  |> permissions.has_capability("reports")
  |> should.equal(True)
}

pub fn has_capability_true_for_admin_test() {
  make_user(["admin"], [], False)
  |> permissions.has_capability("reports")
  |> should.equal(True)
}

pub fn has_capability_false_otherwise_test() {
  make_user(["doctor"], [], False)
  |> permissions.has_capability("reports")
  |> should.equal(False)
}

pub fn reports_route_requires_reports_capability_test() {
  router.requires_capability(router.AdminReports)
  |> should.equal(Some("reports"))
}

pub fn reports_route_not_admin_gated_test() {
  router.requires_admin_role(router.AdminReports)
  |> should.equal(False)
}

pub fn workflow_route_has_no_capability_test() {
  router.requires_capability(router.AdminWorkflow)
  |> should.equal(None)
}
```

- [ ] **Step 2: Run test to verify it fails** (from `clarinet/frontend/`)

Run: `timeout 300 gleam test > /tmp/test-access-capabilities.txt 2>&1`
Expected: FAIL — `has_capability` / `requires_capability` unknown; `AdminReports` still admin-gated.

- [ ] **Step 3: Add `has_capability`** — `permissions.gleam` (after `is_admin_user`, line 10)

```gleam
/// Check whether a user holds a capability. Admins/superusers implicitly hold
/// every capability (the server includes them too); the `is_admin_user` OR is a
/// belt-and-suspenders guard so nav never vanishes if the field is empty.
pub fn has_capability(user: User, capability: String) -> Bool {
  is_admin_user(user) || list.contains(user.capabilities, capability)
}
```

- [ ] **Step 4: Drop reports from `requires_admin_role` + add `requires_capability`** — `router.gleam`

Replace `requires_admin_role` (lines 118-138) with (note: `AdminReports` and `AdminQuartoReports` removed):
```gleam
// Check if route requires admin role
pub fn requires_admin_role(route: Route) -> Bool {
  case route {
    Studies(_)
    | StudyDetail(_)
    | SeriesDetail(_)
    | Records(_)
    | Patients(_)
    | PatientDetail(_)
    | PatientNew
    | RecordNew
    | AdminDashboard(_)
    | AdminRecordTypes
    | AdminRecordTypeDetail(_)
    | AdminRecordTypeEdit(_)
    | AdminWorkflow
    | AdminActivity -> True
    _ -> False
  }
}

// Capability required to view a route, if any. These routes are NOT admin-gated;
// a non-admin holding the capability may enter.
pub fn requires_capability(route: Route) -> Option(String) {
  case route {
    AdminReports | AdminQuartoReports -> Some("reports")
    _ -> None
  }
}
```
(`Option`, `Some`, `None` are already imported at line 5.)

- [ ] **Step 5: Run test to verify it passes** (from `clarinet/frontend/`)

Run: `timeout 120 gleam test > /tmp/test-access-capabilities.txt 2>&1`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add clarinet/frontend/src/utils/permissions.gleam clarinet/frontend/src/router.gleam \
        clarinet/frontend/test/capabilities_test.gleam
git commit -m "feat(frontend): add has_capability and capability-gated routing"
```

---

## Task 9: Frontend — route guards + landing in `main.gleam`

**Files:**
- Modify: `clarinet/frontend/src/main.gleam` (`OnRouteChange` ~243-255; `CheckSessionResult` ~277-304; add `landing_route` helper)

**Interfaces:**
- Consumes: `permissions.has_capability`, `router.requires_capability` (Task 8); `landing_route` (new local helper)

> No unit test — `main.gleam` is the MVU wiring root, verified by `make frontend-check` and the e2e/manual check in Task 11. The frontend type-checks end-to-end, so a wiring mistake is a compile error.

- [ ] **Step 1: Add the `landing_route` helper** — `main.gleam` (place near other top-level helpers, e.g. just below the `update` function or beside `init_page_for_route`)

```gleam
/// Where to send a freshly-authenticated user. Non-admins who only hold the
/// `reports` capability land on the reports page instead of the dashboard.
fn landing_route(model: Model) -> router.Route {
  case model.user {
    Some(u) ->
      case
        !permissions.is_admin_user(u) && permissions.has_capability(u, "reports")
      {
        True -> router.AdminReports
        False -> router.Home
      }
    None -> router.Home
  }
}
```

- [ ] **Step 2: Update the `OnRouteChange` guards** — replace the "Redirect non-admin user away from admin route" block (lines 243-251) with:

```gleam
      // Redirect non-admin user away from admin route
      let is_non_admin = case model.user {
        Some(user) -> !permissions.is_admin_user(user)
        None -> False
      }
      use <- bool.lazy_guard(
        router.requires_admin_role(route) && is_non_admin,
        fn() { redirect_to(landing_route(model)) },
      )

      // Redirect users who lack the capability a route requires.
      let lacks_capability = case router.requires_capability(route), model.user {
        Some(cap), Some(user) -> !permissions.has_capability(user, cap)
        Some(_), None -> True
        None, _ -> False
      }
      use <- bool.lazy_guard(lacks_capability, fn() {
        redirect_to(landing_route(model))
      })

      // Reports-only users bounce off the dashboard to their landing page.
      use <- bool.lazy_guard(
        route == router.Home && landing_route(model) != router.Home,
        fn() { redirect_to(landing_route(model)) },
      )
```

- [ ] **Step 3: Update `CheckSessionResult` landings** — in the `Ok(user)` branch, replace the auth-page redirect (lines 278-281) to use `landing_route`:

```gleam
            False, Some(_), True -> #(
              store.set_route(new_model, landing_route(new_model)),
              modem.push(
                router.route_to_path(landing_route(new_model)),
                option.None,
                option.None,
              ),
            )
```

And replace the `_, _, _ -> { ... }` block (lines 282-303) with a single redirect decision covering admin, capability, and the dashboard bounce:

```gleam
            _, _, _ -> {
              let needs_admin = router.requires_admin_role(route)
              let is_non_admin = case new_model.user {
                Some(user) -> !permissions.is_admin_user(user)
                None -> False
              }
              let lacks_capability = case
                router.requires_capability(route),
                new_model.user
              {
                Some(cap), Some(user) -> !permissions.has_capability(user, cap)
                Some(_), None -> True
                None, _ -> False
              }
              let must_redirect =
                { needs_admin && is_non_admin }
                || lacks_capability
                || { route == router.Home && landing_route(new_model) != router.Home }
              case must_redirect {
                True -> #(
                  store.set_route(new_model, landing_route(new_model)),
                  modem.push(
                    router.route_to_path(landing_route(new_model)),
                    option.None,
                    option.None,
                  ),
                )
                False -> {
                  let #(new_model, page_init_eff) =
                    init_page_for_route(new_model, route)
                  #(new_model, effect.batch([page_init_eff, ensure_sse(new_model)]))
                }
              }
            }
```

- [ ] **Step 4: Type-check** (from `clarinet/frontend/`)

Run: `timeout 120 make frontend-check > /tmp/test-access-capabilities.txt 2>&1`
Expected: 0 errors

- [ ] **Step 5: Commit**

```bash
git add clarinet/frontend/src/main.gleam
git commit -m "feat(frontend): route guards + reports landing for capability users"
```

---

## Task 10: Frontend — reports navigation for capability users

**Files:**
- Modify: `clarinet/frontend/src/components/layout.gleam:24-52` (navbar) + add `reports_only` helper

**Interfaces:**
- Consumes: `permissions.has_capability` (Task 8)

- [ ] **Step 1: Update the navbar menu** — replace the `navbar-menu` div body (lines 32-50) with:

```gleam
    html.div([attribute.class("navbar-menu")], [
      case is_admin(model) {
        True ->
          element.fragment([
            nav_link(route: router.Records(dict.new()), text: t(i18n.NavRecords), current_route: model.route),
            nav_link(route: router.Studies(dict.new()), text: t(i18n.NavStudies), current_route: model.route),
            nav_link(route: router.Patients(dict.new()), text: t(i18n.NavPatients), current_route: model.route),
            nav_link(route: router.AdminRecordTypes, text: t(i18n.NavRecordTypes), current_route: model.route),
            nav_link(route: router.AdminReports, text: t(i18n.NavReports), current_route: model.route),
            nav_link(route: router.AdminQuartoReports, text: t(i18n.NavQuartoReports), current_route: model.route),
            nav_link(route: router.AdminWorkflow, text: t(i18n.NavWorkflow), current_route: model.route),
            nav_link(route: router.AdminActivity, text: t(i18n.NavActivity), current_route: model.route),
            nav_link(route: router.AdminDashboard(dict.new()), text: t(i18n.NavAdmin), current_route: model.route),
          ])
        False ->
          case reports_only(model) {
            True ->
              element.fragment([
                nav_link(route: router.AdminReports, text: t(i18n.NavReports), current_route: model.route),
                nav_link(route: router.AdminQuartoReports, text: t(i18n.NavQuartoReports), current_route: model.route),
              ])
            False -> html.text("")
          }
      },
      locale_switcher(model),
      user_menu(model),
    ]),
```

- [ ] **Step 2: Add the `reports_only` helper** — `layout.gleam` (next to `is_admin`, line 161-167)

```gleam
// Show the reports nav to a non-admin user who holds the reports capability.
fn reports_only(model: Model) -> Bool {
  case model.user {
    Some(user) ->
      !permissions.is_admin_user(user)
      && permissions.has_capability(user, "reports")
    None -> False
  }
}
```
(`Some`/`None` are already imported at line 4.)

- [ ] **Step 3: Type-check** (from `clarinet/frontend/`)

Run: `timeout 120 make frontend-check > /tmp/test-access-capabilities.txt 2>&1`
Expected: 0 errors

- [ ] **Step 4: Commit**

```bash
git add clarinet/frontend/src/components/layout.gleam
git commit -m "feat(frontend): show reports nav to capability-only users"
```

---

## Task 11: Docs + full verification

**Files:**
- Modify: `clarinet/api/CLAUDE.md` (Router Auth Levels table — `reports.py` row)
- Modify: `.claude/rules/api-deps.md` (RBAC section: add `ReportsAccessDep`, `require_capability`, and a `[role_capabilities]` example)

- [ ] **Step 1: Update `clarinet/api/CLAUDE.md`** — in the Router Auth Levels table, change the `reports.py` row:

```
| `reports.py` | `ReportsAccessDep` | Capability-gated: superuser/`admin` OR a role mapped to `reports` in `settings.role_capabilities`. Same guard on `quarto_reports.py` |
```

- [ ] **Step 2: Update `.claude/rules/api-deps.md`** — under "RBAC Dependencies", add:

````markdown
- `require_capability(name)` — dependency factory; admits a user whose effective
  capabilities (`resolve_capabilities`, `clarinet/models/capability.py`) include
  `name`. Superuser/`admin` implicitly hold every capability.
- `ReportsAccessDep = Annotated[User, Depends(require_capability(Capability.REPORTS))]`
  — used by `reports.py` and `quarto_reports.py`.

Projects grant capabilities to roles in `settings.toml`:

```toml
[role_capabilities]
analyst = ["reports"]
```
Roles named here are auto-created at startup; unknown capabilities fail-fast.
````

- [ ] **Step 3: Backend lint/type/format + targeted tests**

Run: `timeout 300 make check > /tmp/test-access-capabilities.txt 2>&1`
Expected: format + lint + typecheck all pass.

Run: `timeout 300 uv run pytest tests/test_capabilities.py tests/test_user_capabilities.py tests/test_require_capability.py tests/test_bootstrap_capabilities.py tests/integration/test_reports.py tests/integration/test_quarto_reports.py -v > /tmp/test-access-capabilities.txt 2>&1`
Expected: all PASS.

> If `make check` re-formats any file, re-`Read` before any further edit (ruff may rewrite line wraps / import order).

- [ ] **Step 4: Frontend build**

Run (from `clarinet/frontend/`): `timeout 300 make frontend-build > /tmp/test-access-capabilities.txt 2>&1`
Expected: builds `clarinet/static/clarinet_frontend.js` with no errors.

- [ ] **Step 5: Commit**

```bash
git add clarinet/api/CLAUDE.md .claude/rules/api-deps.md
git commit -m "docs(auth): document capability-gated reports access"
```

---

## Consumer usage (separate repo — not part of this worktree)

After this lands in core `clarinet`, `clarinet_nir_liver` enables the feature by adding to its `settings.toml`:

```toml
[role_capabilities]
analyst = ["reports"]
```

Then an admin creates the `analyst` role's user(s) and assigns the `analyst` role via the existing user-role admin UI / API (`POST /api/user/{user_id}/roles/analyst`). No code change in the consumer.

---

## Manual / e2e verification (do once, after Task 11)

1. Start the stack with `CLARINET_ROLE_CAPABILITIES='{"analyst":["reports"]}'`, create a non-admin user, assign the `analyst` role.
2. Log in as that user → lands on **Reports**; the nav shows only Reports + Quarto Reports.
3. Reports list + CSV/XLSX download work; Quarto list works.
4. Navigating to `/studies`, `/admin`, `/admin/workflow` redirects back to Reports.
5. Log in as an admin → unchanged: full nav, reports still accessible.

---

## Self-Review (completed during planning)

- **Spec coverage:** capability model (T1), settings (T2), User/UserRead exposure (T3), `require_capability`/`ReportsAccessDep` (T4), guard swap + behaviour (T5), startup validation + role auto-creation (T6), frontend model (T7), helper+routing (T8), guards+landing/D4 (T9), nav (T10), docs + D1/D2/D3 confirmations + consumer note (T11). All spec sections mapped.
- **No migration:** confirmed — no DB schema change anywhere.
- **Type consistency:** `resolve_capabilities(role_names, is_superuser)`, `require_capability(capability)`, `ReportsAccessDep`, `Capability.REPORTS`, `has_capability(user, capability)`, `requires_capability(route) -> Option(String)`, `landing_route(model)` — names used identically across tasks.
- **Backward compat:** every report endpoint's admin/superuser path preserved via `resolve_capabilities` returning all known capabilities for them.
