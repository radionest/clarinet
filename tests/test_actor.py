"""Actor model, the single admin predicate, the access test and the per-request actor."""

from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from clarinet.api.dependencies import get_actor, is_admin
from clarinet.models.actor import (
    HumanActor,
    SystemActor,
    audit_actor_id,
    can_access,
    is_admin_by,
    owner_id_for,
)
from clarinet.models.capability import KNOWN_CAPABILITIES, resolve_capabilities
from clarinet.models.user import UserRole
from tests.utils.factories import make_user


@pytest.mark.parametrize(
    ("is_superuser", "roles", "expected"),
    [
        (True, [], True),
        (True, ["doctor"], True),
        (False, ["admin"], True),
        (False, ["admin", "doctor"], True),
        (False, ["doctor"], False),
        (False, [], False),
    ],
)
def test_every_admin_check_uses_one_predicate(is_superuser, roles, expected):
    assert is_admin_by(is_superuser, roles) is expected
    actor = HumanActor(user_id=uuid4(), is_superuser=is_superuser, role_names=frozenset(roles))
    assert actor.is_admin is expected
    user = make_user(is_superuser=is_superuser)
    user.roles = [UserRole(name=r) for r in roles]
    assert user.is_admin is expected
    assert is_admin(user) is expected
    if expected:
        assert resolve_capabilities(roles, is_superuser) == sorted(KNOWN_CAPABILITIES)


@pytest.mark.parametrize(
    ("role_name", "is_superuser", "roles", "expected"),
    [
        ("doctor", False, ["doctor"], True),
        ("doctor", False, ["admin"], False),  # the admin role alone grants no type
        ("doctor", True, [], True),
        (None, False, ["doctor", "admin"], False),  # a role-less type is superuser-only
        (None, True, [], True),
    ],
)
def test_one_access_test(role_name, is_superuser, roles, expected):
    assert can_access(role_name, is_superuser, roles) is expected


def test_user_as_actor():
    user = make_user(is_superuser=False)
    user.roles = [UserRole(name="doctor")]
    assert user.as_actor() == HumanActor(
        user_id=user.id, is_superuser=False, role_names=frozenset({"doctor"})
    )


def test_audit_actor_and_owner():
    person = HumanActor(user_id=uuid4(), is_superuser=False, role_names=frozenset())
    system = SystemActor(service_user_id=uuid4())
    assert audit_actor_id(person) == person.user_id
    assert audit_actor_id(system) is None
    assert owner_id_for(person) == person.user_id
    assert owner_id_for(system) == system.service_user_id


def _request(headers: dict[str, str], cookies: dict[str, str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        headers=headers, cookies=cookies or {}, client=SimpleNamespace(host="10.0.0.1")
    )


def _user():
    user = make_user(is_superuser=False)
    user.roles = [UserRole(name="doctor")]
    return user


class TestGetActor:
    @pytest.mark.asyncio
    async def test_session_user_is_human(self) -> None:
        user = _user()
        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            actor = await get_actor(_request({}), user)
        assert actor == HumanActor(
            user_id=user.id, is_superuser=False, role_names=frozenset({"doctor"})
        )

    @pytest.mark.asyncio
    async def test_service_token_is_system(self) -> None:
        user = _user()
        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            settings_mock.login_lockout_minutes = 0
            actor = await get_actor(_request({"X-Internal-Token": "secret-token"}), user)
        assert actor == SystemActor(service_user_id=user.id)

    @pytest.mark.asyncio
    async def test_service_token_wins_over_cookie(self) -> None:
        user = _user()
        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            settings_mock.login_lockout_minutes = 0
            request = _request(
                {"X-Internal-Token": "secret-token"}, cookies={"clarinet_session": "c"}
            )
            actor = await get_actor(request, user)
        assert isinstance(actor, SystemActor)

    @pytest.mark.asyncio
    async def test_wrong_token_is_human(self) -> None:
        user = _user()
        with patch("clarinet.api.auth_config.settings") as settings_mock:
            settings_mock.effective_service_token = "secret-token"
            settings_mock.login_lockout_minutes = 0
            actor = await get_actor(_request({"X-Internal-Token": "wrong"}), user)
        assert isinstance(actor, HumanActor)
