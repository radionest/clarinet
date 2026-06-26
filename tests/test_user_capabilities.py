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
