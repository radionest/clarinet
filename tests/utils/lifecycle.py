"""Record-lifecycle test helpers: persisted people, typed records, a recording engine."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from uuid import UUID

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from clarinet.models import Record, RecordRead, RecordStatus, User
from clarinet.models.actor import HumanActor, SystemActor
from clarinet.repositories.record_event_repository import RecordEventRepository
from clarinet.repositories.record_repository import RecordRepository
from clarinet.services.record_service import RecordService
from clarinet.settings import Settings
from tests.conftest import create_authenticated_client
from tests.utils.factories import make_record_type, seed_record

ROLE = "lc-role"
OTHER_ROLE = "lc-other-role"


async def force_status(
    session: AsyncSession, record: Record, status: RecordStatus, **columns: object
) -> None:
    """Test setup only: put a saved record in ``status`` without the lifecycle.

    The ORM guard refuses ``record.status = …`` on a saved record; this goes
    around it with a Core UPDATE, commits and refreshes ``record``.
    """
    await session.execute(
        update(Record).where(col(Record.id) == record.id).values(status=status, **columns)
    )
    await session.commit()
    await session.refresh(record)


def human(user: User) -> HumanActor:
    """HumanActor for a user whose roles are loaded (``create_mock_user_with_role``)."""
    return user.as_actor()


@asynccontextmanager
async def client_as(
    user: User, session: AsyncSession, settings: Settings, *, service_token: str | None = None
) -> AsyncIterator[AsyncClient]:
    """Authenticated client for ``user`` — one at a time (dependency overrides are app-global).

    With ``service_token`` every request also carries ``X-Internal-Token``, so the
    record service sees a ``SystemActor`` (``get_actor`` reads only the header).
    """
    clients = create_authenticated_client(user, session, settings)
    client = await anext(clients)
    if service_token is not None:
        client.headers["X-Internal-Token"] = service_token
    try:
        yield client
    finally:
        with suppress(StopAsyncIteration):
            await anext(clients)  # runs the generator's override cleanup


@dataclass
class RecordingEngine:
    """RecordFlowEngine stand-in: logs (trigger, record id, record status at dispatch)."""

    calls: list[tuple[str, int, RecordStatus]] = field(default_factory=list)

    async def handle_record_status_change(
        self, record: RecordRead, old_status: RecordStatus | None = None
    ) -> None:
        self.calls.append(("status", record.id, record.status))

    async def handle_record_invalidation(
        self, record: RecordRead, old_status: RecordStatus | None = None
    ) -> None:
        self.calls.append(("invalidation", record.id, record.status))

    async def handle_record_data_update(self, record: RecordRead) -> None:
        self.calls.append(("data_update", record.id, record.status))

    async def handle_record_file_change(self, record: RecordRead) -> None:
        self.calls.append(("file_change", record.id, record.status))

    async def handle_file_update(
        self, file_name: str, patient_id: str, source_record: RecordRead | None = None
    ) -> None:
        return None


@dataclass
class Lifecycle:
    """One series, persisted people for every actor kind, and a service factory."""

    session: AsyncSession
    patient_id: str
    study_uid: str
    series_uid: str
    owner: User  # ROLE
    colleague: User  # ROLE
    outsider: User  # OTHER_ROLE only
    admin: User  # 'admin' + ROLE, not a superuser
    admin_only: User  # 'admin' without ROLE, not a superuser
    superuser: User  # superuser holding only OTHER_ROLE (no 'admin' role)
    service_user: User  # the admin row a service token resolves to (superuser)
    engine: RecordingEngine = field(default_factory=RecordingEngine)

    @property
    def system(self) -> SystemActor:
        return SystemActor(service_user_id=self.service_user.id)

    def service(self) -> RecordService:
        return RecordService(
            RecordRepository(self.session),
            self.engine,
            event_repo=RecordEventRepository(self.session),
        )

    async def record_type(self, name: str, **kw: object) -> str:
        """Persist a SERIES type with ROLE and no uniqueness partitions; return its name."""
        self.session.add(make_record_type(name, role_name=ROLE, unique_by=None, **kw))
        await self.session.commit()
        return name

    async def seed(
        self,
        rt_name: str,
        *,
        status: RecordStatus = RecordStatus.pending,
        user_id: UUID | None = None,
        **kw: object,
    ) -> Record:
        return await seed_record(
            self.session,
            self.patient_id,
            self.study_uid,
            self.series_uid,
            rt_name,
            status=status,
            user_id=user_id,
            **kw,
        )
