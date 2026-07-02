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
