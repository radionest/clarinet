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
