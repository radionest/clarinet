"""Who is acting on a record, and who may access one.

A request is either a **system actor** — it carries a valid ``X-Internal-Token``
(RecordFlow, pipeline workers, cron, operator scripts) — or a **human actor**, the
session user. ``api/dependencies.py::get_actor`` builds exactly one per request;
every mutating ``RecordService`` method takes it as a required keyword-only
argument. ``is_admin_by`` is the only definition of "admin"; ``can_access`` is the
only access test (read gate, a person's mutation rights, new-owner eligibility).
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import assert_never
from uuid import UUID

ADMIN_ROLE = "admin"


def is_admin_by(is_superuser: bool, role_names: Collection[str]) -> bool:
    """Superuser OR member of the built-in ``admin`` role."""
    return is_superuser or ADMIN_ROLE in role_names


def can_access(role_name: str | None, is_superuser: bool, role_names: Collection[str]) -> bool:
    """Superuser, or a holder of the record type's role.

    ``role_name=None`` types are superuser-only. The ``admin`` role alone grants
    no type — it widens rights on records the user can already access.
    """
    return is_superuser or (role_name is not None and role_name in role_names)


@dataclass(frozen=True, slots=True)
class SystemActor:
    """A service-token request.

    ``service_user_id`` is the admin row the token resolves to. It is written only
    as the owner of an unassigned record the system claims or submits — never as
    the audit actor (system events carry ``actor_id=None``).
    """

    service_user_id: UUID


@dataclass(frozen=True, slots=True)
class HumanActor:
    """The session user, snapshotted once per request — no ORM, no lazy loads."""

    user_id: UUID
    is_superuser: bool
    role_names: frozenset[str]

    @property
    def is_admin(self) -> bool:
        return is_admin_by(self.is_superuser, self.role_names)


type Actor = SystemActor | HumanActor


def audit_actor_id(actor: Actor) -> UUID | None:
    """``RecordEvent.actor_id`` for this actor: the person, ``None`` for the system."""
    match actor:
        case HumanActor(user_id=user_id):
            return user_id
        case SystemActor():
            return None
        case _:
            assert_never(actor)


def owner_id_for(actor: Actor) -> UUID:
    """The user a claim, or a submit of an unassigned record, makes the owner."""
    match actor:
        case HumanActor(user_id=user_id):
            return user_id
        case SystemActor(service_user_id=service_user_id):
            return service_user_id
        case _:
            assert_never(actor)
