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
