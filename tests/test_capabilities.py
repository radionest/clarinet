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
