"""Repository for Record-specific database operations.

NOTE: selectinload() calls use ``# type: ignore`` — mypy cannot resolve
SQLAlchemy InstrumentedAttribute on SQLModel classes (known limitation).
"""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, assert_never
from uuid import UUID

from sqlalchemy import and_, distinct, exists, func, literal, or_, tuple_
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, aliased, selectinload
from sqlmodel import col, select
from sqlmodel.sql.expression import SelectOfScalar

from clarinet.exceptions.domain import (
    RecordConstraintViolationError,
    RecordLimitReachedError,
    RecordNotFoundError,
    RecordParentRequiredError,
    RecordTypeNotFoundError,
    RecordUniquePerUserError,
    UserNotFoundError,
    ValidationError,
)
from clarinet.models import Record
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.file_schema import FileRole, RecordFileLink, RecordTypeFileLink
from clarinet.models.patient import Patient
from clarinet.models.record import RecordFindResult, RecordFindResultComparisonOperator, RecordType
from clarinet.models.study import Series, Study
from clarinet.models.user import User, UserRolesLink
from clarinet.repositories.base import BaseRepository
from clarinet.services.events.capture import emit_entity
from clarinet.settings import settings
from clarinet.types import RecordData
from clarinet.utils.logger import logger
from clarinet.utils.pagination import (
    InvalidCursorError,
    SortOrder,
    decode_cursor,
    encode_cursor,
)


@dataclass
class RecordSearchCriteria:
    """Search criteria for finding records."""

    patient_id: str | None = None
    patient_anon_id: str | None = None
    series_uid: str | None = None
    anon_series_uid: str | None = None
    study_uid: str | None = None
    anon_study_uid: str | None = None
    user_id: UUID | None = None
    record_type_name: str | None = None
    record_status: RecordStatus | None = None
    parent_record_id: int | None = None
    wo_user: bool | None = None
    include_unassigned: bool = False
    random_one: bool = False
    role_names: set[str] | None = None
    # Hides unassigned records of unique_per_user types where ``user_id``
    # already has a record at the matching DICOM level. Requires ``user_id``
    # to be set; ``_build_criteria_query`` joins ``RecordType`` automatically.
    exclude_unique_violations: bool = False
    data_queries: list[RecordFindResult] = field(default_factory=list)


@dataclass
class RecordPageResult:
    """Result of cursor-based paginated record search."""

    records: Sequence[Record]
    next_cursor: str | None


@dataclass
class RecordFilterScope:
    """Distinct patient/record_type/user values within a search-criteria scope.

    Used by ``RecordRepository.get_filter_options`` to populate filter
    dropdowns. All ID lists contain raw values (no display labels) and
    are pre-sorted; ``has_unassigned`` signals whether the scope contains
    any record with ``user_id IS NULL``.
    """

    patients: list[str]
    record_types: list[str]
    users: list[str]
    has_unassigned: bool


class _SortColumnKind(Enum):
    """Discriminator for how `_SortSpec.column()` resolves into a SQL expression."""

    NONE = "none"
    """`id_asc` / `id_desc` / `changed_at_desc` — no auxiliary ORDER BY column."""

    RECORD_COLUMN = "record_column"
    """`column_attr` is an InstrumentedAttribute on `Record` / `RecordType`."""

    MODALITY_ALIAS = "modality_alias"
    """Resolves against the per-query `aliased(Series)` outerjoin created in
    `find_page`. `extract_key` for the modality specs reads `r.series.modality`
    from the eager-loaded relationship, while ORDER BY reads from the outerjoin
    alias. Both resolve to the same row inside a single transaction, but the
    cursor extraction depends on `selectinload(Record.series)` staying in
    `_build_criteria_query` — removing it would cause a `MissingGreenlet`
    lazy-load in async context."""


@dataclass(frozen=True)
class _SortSpec:
    """How to materialize one SortOrder into ORDER BY / WHERE / cursor key."""

    kind: _SortColumnKind
    column_attr: Any
    ascending: bool
    nullable: bool
    extract_key: Callable[[Record], Any]
    cast_cursor: Callable[[Any], Any] = lambda raw: raw

    def __post_init__(self) -> None:
        # Pair the discriminator with its payload — guards against future
        # _SORT_SPECS entries silently dropping a required column or carrying
        # one for a kind that ignores it.
        if self.kind is _SortColumnKind.RECORD_COLUMN:
            if self.column_attr is None:
                raise ValueError("RECORD_COLUMN sort spec requires column_attr")
        elif self.column_attr is not None:
            raise ValueError(f"{self.kind} sort spec must not carry column_attr")

    def column(self, series_alias: Any) -> Mapped[Any] | None:
        """Return the SQL column expression for ORDER BY / WHERE."""
        match self.kind:
            case _SortColumnKind.NONE:
                return None
            case _SortColumnKind.MODALITY_ALIAS:
                return col(series_alias.modality)
            case _SortColumnKind.RECORD_COLUMN:
                return col(self.column_attr)
            case _:
                assert_never(self.kind)


_SORT_SPECS: dict[SortOrder, _SortSpec] = {
    "changed_at_desc": _SortSpec(
        kind=_SortColumnKind.NONE,  # special-cased in find_page (legacy id DESC tie-break)
        column_attr=None,
        ascending=False,
        nullable=False,
        extract_key=lambda r: r.changed_at,
    ),
    "id_asc": _SortSpec(
        kind=_SortColumnKind.NONE,
        column_attr=None,
        ascending=True,
        nullable=False,
        extract_key=lambda _r: None,
    ),
    "id_desc": _SortSpec(
        kind=_SortColumnKind.NONE,
        column_attr=None,
        ascending=False,
        nullable=False,
        extract_key=lambda _r: None,
    ),
    "record_type_asc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=RecordType.name,
        ascending=True,
        nullable=False,
        extract_key=lambda r: r.record_type_name,
    ),
    "record_type_desc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=RecordType.name,
        ascending=False,
        nullable=False,
        extract_key=lambda r: r.record_type_name,
    ),
    "status_asc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=Record.status,
        ascending=True,
        nullable=False,
        extract_key=lambda r: r.status,
        cast_cursor=lambda raw: RecordStatus(raw),
    ),
    "status_desc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=Record.status,
        ascending=False,
        nullable=False,
        extract_key=lambda r: r.status,
        cast_cursor=lambda raw: RecordStatus(raw),
    ),
    "patient_asc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=Record.patient_id,
        ascending=True,
        nullable=False,
        extract_key=lambda r: r.patient_id,
    ),
    "patient_desc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=Record.patient_id,
        ascending=False,
        nullable=False,
        extract_key=lambda r: r.patient_id,
    ),
    "user_asc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=Record.user_id,
        ascending=True,
        nullable=True,
        extract_key=lambda r: r.user_id,
        cast_cursor=lambda raw: UUID(raw),
    ),
    "user_desc": _SortSpec(
        kind=_SortColumnKind.RECORD_COLUMN,
        column_attr=Record.user_id,
        ascending=False,
        nullable=True,
        extract_key=lambda r: r.user_id,
        cast_cursor=lambda raw: UUID(raw),
    ),
    "modality_asc": _SortSpec(
        kind=_SortColumnKind.MODALITY_ALIAS,
        column_attr=None,
        ascending=True,
        nullable=True,
        extract_key=lambda r: r.series.modality if r.series else None,
    ),
    "modality_desc": _SortSpec(
        kind=_SortColumnKind.MODALITY_ALIAS,
        column_attr=None,
        ascending=False,
        nullable=True,
        extract_key=lambda r: r.series.modality if r.series else None,
    ),
}


def _keyset_where(
    *,
    sort: SortOrder,
    sort_col: Any,
    cursor_key: Any,
    cursor_id: int,
) -> Any:
    """Build the keyset WHERE clause for paging past (cursor_key, cursor_id)."""
    if sort == "changed_at_desc":
        cursor_ts = datetime.fromisoformat(cursor_key) if cursor_key else None
        return tuple_(col(Record.changed_at), col(Record.id)) < tuple_(
            literal(cursor_ts), literal(cursor_id)
        )
    if sort == "id_asc":
        return col(Record.id) > literal(cursor_id)
    if sort == "id_desc":
        return col(Record.id) < literal(cursor_id)

    spec = _SORT_SPECS[sort]
    ascending = spec.ascending
    # Reject a tampered cursor that carries `k: null` for a non-nullable
    # sort column — without the guard the SQL would collapse to `col > NULL`
    # which is always NULL → zero rows returned silently. Surface the
    # corruption to the API client instead of an empty page.
    if cursor_key is None and not spec.nullable:
        raise InvalidCursorError(f"Cursor for non-nullable sort '{sort}' carries a null key")
    casted_key = spec.cast_cursor(cursor_key) if cursor_key is not None else None

    if not spec.nullable:
        cmp_op = sort_col > literal(casted_key) if ascending else sort_col < literal(casted_key)
        return or_(
            cmp_op,
            and_(sort_col == literal(casted_key), col(Record.id) > literal(cursor_id)),
        )

    # Nullable column with NULLS LAST: NULL rows come after all non-NULL.
    if casted_key is None:
        # Cursor in NULL zone — only NULL rows past the id.
        return and_(sort_col.is_(None), col(Record.id) > literal(cursor_id))

    cmp_op = sort_col > literal(casted_key) if ascending else sort_col < literal(casted_key)
    return or_(
        cmp_op,
        and_(sort_col == literal(casted_key), col(Record.id) > literal(cursor_id)),
        sort_col.is_(None),
    )


_COMPARISON_OPS: dict[RecordFindResultComparisonOperator, Callable[..., Any]] = {
    RecordFindResultComparisonOperator.eq: lambda f, v: f == v,
    RecordFindResultComparisonOperator.gt: lambda f, v: f > v,
    RecordFindResultComparisonOperator.lt: lambda f, v: f < v,
    RecordFindResultComparisonOperator.contains: lambda f, v: f.like(f"%{v}%"),
}


def _record_type_with_files() -> Any:
    """Return selectinload chain for record_type → file_links → file_definition."""
    return (
        selectinload(Record.record_type)  # type: ignore[arg-type]
        .selectinload(RecordType.file_links)  # type: ignore[arg-type]
        .selectinload(RecordTypeFileLink.file_definition)  # type: ignore[arg-type]
    )


def _record_file_links_eager_load() -> Any:
    """Return selectinload chain for record → file_links → file_definition."""
    return (
        selectinload(Record.file_links).selectinload(RecordFileLink.file_definition)  # type: ignore[arg-type]  # type: ignore[arg-type]
    )


def _unique_per_user_violation_filter(user_id: UUID) -> Any:
    """Build NOT(...) filter excluding unassigned records violating unique_per_user.

    Returns a condition that, when applied to a query containing Record joined
    with RecordType, excludes unassigned records of unique_per_user types where
    the given user already has a record in the same DICOM context.
    """
    inner_r = aliased(Record, flat=True)
    inner_subq = (
        select(literal(1))
        .select_from(inner_r)
        .where(
            col(inner_r.user_id) == user_id,
            inner_r.record_type_name == Record.record_type_name,
            or_(
                and_(
                    col(RecordType.level) == "SERIES",
                    inner_r.series_uid == Record.series_uid,  # type: ignore[arg-type]
                ),
                and_(
                    col(RecordType.level) == "STUDY",
                    inner_r.study_uid == Record.study_uid,  # type: ignore[arg-type]
                ),
                and_(
                    col(RecordType.level) == "PATIENT",
                    inner_r.patient_id == Record.patient_id,  # type: ignore[arg-type]
                ),
            ),
        )
        .correlate(Record, RecordType)
    )
    return ~and_(
        col(Record.user_id).is_(None),
        col(RecordType.unique_per_user).is_(True),
        exists(inner_subq),
    )


class RecordRepository(BaseRepository[Record]):
    """Repository for Record model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize record repository with session."""
        super().__init__(session, Record)

    async def get(self, record_id: int) -> Record:
        """Get record by ID.

        Args:
            record_id: Record ID

        Returns:
            Found record

        Raises:
            RecordNotFoundError: If record doesn't exist
        """
        record = await self.session.get(Record, record_id)
        if not record:
            raise RecordNotFoundError(record_id)
        return record

    async def get_with_record_type(self, record_id: int) -> Record:
        """Get record with record_type relation loaded.

        Args:
            record_id: Record ID

        Returns:
            Record with record_type loaded

        Raises:
            RecordNotFoundError: If record doesn't exist
        """
        statement = (
            select(Record)
            .where(Record.id == record_id)
            .options(_record_type_with_files(), _record_file_links_eager_load())
        )
        result = await self.session.execute(statement)
        record = result.scalars().first()
        if not record:
            raise RecordNotFoundError(record_id)
        return record

    async def get_with_relations(self, record_id: int, *, lock: bool = False) -> Record:
        """Get a single record with all relationships eagerly loaded.

        Args:
            record_id: Record ID
            lock: When ``True``, acquire a row-level lock on the record
                (``SELECT ... FOR UPDATE``). Caller must keep the transaction
                open until the lock is no longer needed.

        Returns:
            Record with patient, study, series, and record_type loaded

        Raises:
            RecordNotFoundError: If record doesn't exist
        """
        statement = (
            select(Record)
            .where(Record.id == record_id)
            .options(
                selectinload(Record.patient),  # type: ignore
                selectinload(Record.study),  # type: ignore
                selectinload(Record.series),  # type: ignore
                _record_type_with_files(),
                _record_file_links_eager_load(),
            )
        )
        if lock:
            statement = statement.with_for_update()
        result = await self.session.execute(statement)
        record = result.scalars().first()
        if not record:
            raise RecordNotFoundError(record_id)
        return record

    # FASTFIX: default limit raised from 100 to 10_000 to unblock SPA list views.
    # Frontend currently loads everything into a global Model and filters client-side,
    # so records with id > 100 were invisible even when the displayed subset was <100.
    # Proper fix: PaginationDep on endpoints + scoped cache refactor (see PR discussion).
    async def get_all_with_relations(self, skip: int = 0, limit: int = 10_000) -> Sequence[Record]:
        """Get all records with all relationships eagerly loaded.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of records with patient, study, series, and record_type loaded
        """
        statement = select(Record).options(
            selectinload(Record.patient),  # type: ignore
            selectinload(Record.study),  # type: ignore
            selectinload(Record.series),  # type: ignore
            _record_type_with_files(),
            _record_file_links_eager_load(),
        )
        statement = self._paginate(statement, skip, limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_all_for_user_roles(
        self, role_names: set[str], skip: int = 0, limit: int = 10_000
    ) -> Sequence[Record]:
        """Get all records whose RecordType.role_name matches one of the given roles.

        Records with role_name=NULL are excluded (superuser-only).

        Args:
            role_names: Set of role names to filter by
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of records with patient, study, series, and record_type loaded
        """
        statement = (
            select(Record)
            .join(RecordType)
            .where(col(RecordType.role_name).in_(list(role_names)))
            .options(
                selectinload(Record.patient),  # type: ignore
                selectinload(Record.study),  # type: ignore
                selectinload(Record.series),  # type: ignore
                _record_type_with_files(),
                _record_file_links_eager_load(),
            )
        )
        statement = self._paginate(statement, skip, limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def find_by_user(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 10_000,
        role_names: set[str] | None = None,
        include_unassigned: bool = False,
        exclude_unique_violations: bool = False,
    ) -> Sequence[Record]:
        """Find records assigned to a specific user with relations loaded.

        Args:
            user_id: User UUID to filter by
            skip: Number of records to skip
            limit: Maximum number of records to return
            role_names: Optional set of role names to filter by
            include_unassigned: If True, also include records with user_id=NULL
            exclude_unique_violations: If True, hide unassigned records that
                violate unique_per_user for this user

        Returns:
            List of records with patient, study, series, and record_type loaded
        """
        user_filter = (
            or_(col(Record.user_id) == user_id, col(Record.user_id).is_(None))
            if include_unassigned
            else col(Record.user_id) == user_id
        )
        # Join RecordType unconditionally when filtering by role or unique_per_user
        needs_join = role_names is not None or exclude_unique_violations
        statement = select(Record).where(user_filter)
        if needs_join:
            statement = statement.join(RecordType)
        statement = statement.options(
            selectinload(Record.patient),  # type: ignore
            selectinload(Record.study),  # type: ignore
            selectinload(Record.series),  # type: ignore
            _record_type_with_files(),
            _record_file_links_eager_load(),
        )
        statement = self._paginate(statement, skip, limit)
        if role_names is not None:
            statement = statement.where(col(RecordType.role_name).in_(list(role_names)))
        if exclude_unique_violations:
            statement = statement.where(_unique_per_user_violation_filter(user_id))
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def find_pending_by_user(
        self,
        user_id: UUID,
        role_names: set[str] | None = None,
        include_unassigned: bool = False,
        exclude_unique_violations: bool = False,
    ) -> Sequence[Record]:
        """Find active (non-terminal) records assigned to a user with relations loaded.

        Returns records that are not preparing, blocked, finished, failed, or paused.

        Args:
            user_id: User UUID to filter by
            role_names: Optional set of role names to filter by
            include_unassigned: If True, also include records with user_id=NULL
            exclude_unique_violations: If True, hide unassigned records that
                violate unique_per_user for this user

        Returns:
            List of active records with patient, study, series, and record_type loaded
        """
        user_filter = (
            or_(col(Record.user_id) == user_id, col(Record.user_id).is_(None))
            if include_unassigned
            else col(Record.user_id) == user_id
        )
        needs_join = role_names is not None or exclude_unique_violations
        statement = select(Record).where(
            user_filter,
            Record.status != RecordStatus.preparing,
            Record.status != RecordStatus.blocked,
            Record.status != RecordStatus.finished,
            Record.status != RecordStatus.failed,
            Record.status != RecordStatus.pause,
        )
        if needs_join:
            statement = statement.join(RecordType)
        statement = statement.options(
            selectinload(Record.patient),  # type: ignore
            selectinload(Record.study),  # type: ignore
            selectinload(Record.series),  # type: ignore
            _record_type_with_files(),
            _record_file_links_eager_load(),
        )
        if role_names is not None:
            statement = statement.where(col(RecordType.role_name).in_(list(role_names)))
        if exclude_unique_violations:
            statement = statement.where(_unique_per_user_violation_filter(user_id))
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def create_with_relations(self, record: Record) -> Record:
        """Create a record and return it with all relations loaded.

        Args:
            record: Record to create

        Returns:
            Created record with all relations loaded
        """
        self.session.add(record)
        await self.session.commit()
        return await self.get_with_relations(record.id)  # type: ignore

    async def update_status(
        self, record_id: int, new_status: RecordStatus
    ) -> tuple[Record, RecordStatus]:
        """Update record status.

        Args:
            record_id: Record ID
            new_status: New status to set

        Returns:
            Tuple of (record with relations loaded, old status)

        Raises:
            RecordNotFoundError: If record doesn't exist
        """
        record = await self.get(record_id)
        old_status = record.status
        record.status = new_status
        await self.session.commit()
        return await self.get_with_relations(record_id), old_status

    async def update_data(
        self,
        record_id: int,
        data: RecordData,
        new_status: RecordStatus | None = None,
    ) -> tuple[Record, RecordStatus]:
        """Update record data and optionally status.

        Args:
            record_id: Record ID
            data: New record data
            new_status: Optional new status to set

        Returns:
            Tuple of (record with relations loaded, old status)

        Raises:
            RecordNotFoundError: If record doesn't exist
        """
        record = await self.get(record_id)
        old_status = record.status
        record.data = data
        if new_status is not None:
            record.status = new_status
        await self.session.commit()
        return await self.get_with_relations(record_id), old_status

    async def update_fields(self, record_id: int, update_data: dict[str, Any]) -> Record:
        """Update arbitrary fields on a record.

        Args:
            record_id: Record ID
            update_data: Dictionary of field names to new values

        Returns:
            Updated record with relations loaded

        Raises:
            RecordNotFoundError: If record doesn't exist
        """
        record = await self.get(record_id)
        for key, value in update_data.items():
            setattr(record, key, value)
        await self.session.commit()
        return await self.get_with_relations(record_id)

    async def update_checksums(self, record: Record, checksums: dict[str, str]) -> None:
        """Update file checksums on existing RecordFileLink rows.

        Args:
            record: Record with eager-loaded file_links
            checksums: New checksums dict keyed by file definition name for
                singular files and ``"name:filename"`` for collections
                (``multiple=True``) — the link's stored filename picks its key.
        """
        for link in record.file_links or []:
            name = link.file_definition.name
            key = name if name in checksums else f"{name}:{link.filename}"
            if key in checksums:
                link.checksum = checksums[key]
        await self.session.commit()

    async def add_file_links(
        self,
        record: Record,
        matched_files: dict[str, str],
    ) -> int:
        """Create RecordFileLink rows for definitions that have no link yet.

        Additive counterpart of ``set_files`` — existing links (and their
        checksums) stay untouched, so OUTPUT files discovered after record
        creation can be registered without wiping INPUT links. Already-linked
        definitions are detected with a direct SELECT (the eagerly loaded
        ``record.file_links`` may be stale within a request). New links are
        attached via relationship objects, so ``record.file_links`` reflects
        them in memory and a follow-up ``update_checksums`` on the same
        instance sees them without a re-fetch.

        A concurrent writer inserting the same (record_id, file_definition_id)
        PK first wins the race: the IntegrityError is rolled back and the
        record is reloaded in place (rollback expires it), so callers can keep
        using the instance — the links exist either way.

        Args:
            record: Record with ``record_type.file_links`` and ``file_links``
                eagerly loaded.
            matched_files: Dict mapping file definition name to matched filename.

        Returns:
            Number of links created (0 when all existed or the race was lost).
        """
        record_id = record.id
        fd_map = {
            link.file_definition.name: link.file_definition
            for link in record.record_type.file_links
        }
        result = await self.session.execute(
            select(RecordFileLink.file_definition_id).where(
                col(RecordFileLink.record_id) == record_id
            )
        )
        linked_ids = set(result.scalars())
        created = 0
        for name, filename in matched_files.items():
            fd = fd_map.get(name)
            if fd is None or fd.id in linked_ids:
                continue
            self.session.add(RecordFileLink(record=record, file_definition=fd, filename=filename))
            created += 1
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            assert record_id is not None
            await self.get_with_relations(record_id)
            logger.warning(
                f"Record {record_id}: lost file-link creation race to a concurrent writer"
            )
            return 0
        return created

    async def set_files(
        self,
        record: Record,
        matched_files: dict[str, str],
    ) -> None:
        """Set matched files on a record by creating RecordFileLink rows.

        Builds the FileDefinition lookup from the record's eagerly loaded
        ``record_type.file_links`` chain — no extra DB query needed.

        Args:
            record: Record with ``record_type.file_links`` eagerly loaded
                (via ``get_with_relations()`` or ``create_with_relations()``).
            matched_files: Dict mapping file definition name to matched filename.
        """
        # Build name → FileDefinition map from eager-loaded M2M links
        fd_map = {
            link.file_definition.name: link.file_definition
            for link in record.record_type.file_links
        }

        # Remove existing file links
        for link in list(record.file_links or []):
            await self.session.delete(link)
        await self.session.flush()

        # Create new file links
        for name, filename in matched_files.items():
            fd = fd_map[name]
            link = RecordFileLink(
                record_id=record.id,
                file_definition_id=fd.id,
                filename=filename,
            )
            self.session.add(link)
        await self.session.commit()

    async def delete_output_file_links(self, record: Record) -> int:
        """Delete RecordFileLink rows for OUTPUT file definitions.

        Uses a single SQL DELETE to avoid race conditions with concurrent
        writers (e.g. pipeline tasks creating file links).

        Args:
            record: Record with eager-loaded record_type.file_links.

        Returns:
            Number of deleted RecordFileLink rows.
        """
        output_fd_ids = {
            link.file_definition_id
            for link in record.record_type.file_links
            if link.role == FileRole.OUTPUT
        }
        if not output_fd_ids:
            return 0

        stmt = sa_delete(RecordFileLink).where(
            col(RecordFileLink.record_id) == record.id,
            col(RecordFileLink.file_definition_id).in_(output_fd_ids),
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def assign_user(self, record_id: int, user_id: UUID) -> tuple[Record, RecordStatus]:
        """Assign a user to a record and set status to inwork.

        Args:
            record_id: Record ID
            user_id: User UUID

        Returns:
            Tuple of (record with relations loaded, old status)

        Raises:
            RecordNotFoundError: If record doesn't exist
            UserNotFoundError: If user doesn't exist
            ValidationError: If record is blocked or preparing
        """
        record = await self.get(record_id)
        if record.status in (RecordStatus.blocked, RecordStatus.preparing):
            raise ValidationError(f"Cannot assign user to a {record.status.value} record")
        user = await self.session.get(User, user_id)
        if not user:
            raise UserNotFoundError(user_id)
        old_status = record.status
        record.user_id = user_id
        record.status = RecordStatus.inwork
        await self.session.commit()
        return await self.get_with_relations(record_id), old_status

    async def unassign_user(self, record_id: int) -> tuple[Record, RecordStatus]:
        """Remove user assignment from a record.

        If the record is currently inwork, status is reset to pending.

        Args:
            record_id: Record ID.

        Returns:
            Tuple of (record with relations loaded, old status).

        Raises:
            RecordNotFoundError: If record doesn't exist.
        """
        record = await self.get(record_id)
        old_status = record.status
        record.user_id = None
        if record.status == RecordStatus.inwork:
            record.status = RecordStatus.pending
        await self.session.commit()
        return await self.get_with_relations(record_id), old_status

    async def ensure_user_assigned(self, record_id: int, user_id: UUID) -> None:
        """Assign user to a record only if it has no user yet.

        Args:
            record_id: Record ID.
            user_id: User UUID to assign.
        """
        record = await self.get(record_id)
        if record.user_id is None:
            record.user_id = user_id
            await self.session.commit()

    async def claim_record(self, record_id: int, user_id: UUID) -> Record:
        """Assign user and set status to inwork.

        Args:
            record_id: Record ID
            user_id: User UUID

        Returns:
            Updated record

        Raises:
            RecordNotFoundError: If record doesn't exist
            ValidationError: If record is blocked or preparing
        """
        record = await self.get(record_id)
        if record.status in (RecordStatus.blocked, RecordStatus.preparing):
            raise ValidationError(f"Cannot claim a {record.status.value} record")
        record.user_id = user_id
        record.status = RecordStatus.inwork
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def bulk_update_status(self, record_ids: list[int], new_status: RecordStatus) -> None:
        """Update status for multiple records.

        Records that don't exist are silently skipped.

        Args:
            record_ids: List of record IDs
            new_status: New status to set
        """
        for record_id in record_ids:
            record = await self.get_optional(record_id)
            if record:
                record.status = new_status
        await self.session.commit()

    async def invalidate_record(
        self,
        record_id: int,
        mode: str,
        source_record_id: int | None = None,
        reason: str | None = None,
    ) -> Record:
        """Invalidate a record by resetting its status and/or appending reason.

        Args:
            record_id: ID of the record to invalidate.
            mode: "hard" resets status to pending (keeps user_id); a
                  ``preparing`` record keeps its status — preparation owns the
                  exit, only the reason is appended.
                  "soft" only appends reason to context_info.
            source_record_id: ID of the record that triggered invalidation.
            reason: Human-readable reason. Defaults to a generated message.

        Returns:
            Updated record with relations loaded.

        Raises:
            RecordNotFoundError: If record doesn't exist.
        """
        record = await self.get(record_id)

        if reason is None and source_record_id is not None:
            reason = f"Invalidated by record #{source_record_id}"

        if reason:
            if record.context_info:
                record.context_info = f"{record.context_info}\n{reason}"
            else:
                record.context_info = reason

        if mode == "hard" and record.status != RecordStatus.preparing:
            record.status = RecordStatus.pending

        await self.session.commit()
        return await self.get_with_relations(record_id)

    async def fail_record(self, record_id: int, reason: str) -> tuple[Record, RecordStatus]:
        """Mark a record as failed with a reason appended to context_info.

        Args:
            record_id: ID of the record to fail.
            reason: Human-readable reason for failure.

        Returns:
            Tuple of (record with relations loaded, old status).

        Raises:
            RecordNotFoundError: If record doesn't exist.
        """
        record = await self.get(record_id)
        old_status = record.status

        prefixed = f"Manually failed: {reason}"
        if record.context_info:
            record.context_info = f"{record.context_info}\n{prefixed}"
        else:
            record.context_info = prefixed

        record.status = RecordStatus.failed
        await self.session.commit()
        return await self.get_with_relations(record_id), old_status

    async def count_by_type_and_context(
        self,
        record_type_name: str,
        patient_id: str | None,
        study_uid: str | None,
        series_uid: str | None,
        level: DicomQueryLevel,
    ) -> int:
        """Count records matching type at the given DicomQueryLevel context.

        Context key depends on level:
          - PATIENT: (record_type_name, patient_id)
          - STUDY:   (record_type_name, study_uid)
          - SERIES:  (record_type_name, series_uid)

        Args:
            record_type_name: Record type name to filter by.
            patient_id: Patient identifier (used for PATIENT level).
            study_uid: Study UID (used for STUDY level).
            series_uid: Series UID (used for SERIES level).
            level: DicomQueryLevel selecting which column to scope by.

        Returns:
            Number of matching records.
        """
        query = (
            select(func.count(col(Record.id)))
            .join(RecordType)
            .where(RecordType.name == record_type_name)
        )
        match level:
            case DicomQueryLevel.PATIENT:
                query = query.where(Record.patient_id == patient_id)
            case DicomQueryLevel.STUDY:
                query = query.where(Record.study_uid == study_uid)
            case DicomQueryLevel.SERIES:
                query = query.where(Record.series_uid == series_uid)
            case _:
                raise ValueError(f"Unsupported level for record count: {level}")
        result = await self.session.execute(query)
        return result.scalar_one()

    async def count_user_records_for_context(
        self,
        user_id: UUID,
        record_type_name: str,
        patient_id: str,
        study_uid: str | None,
        series_uid: str | None,
        level: str,
    ) -> int:
        """Count records assigned to a user for a given type and DICOM context.

        Context key depends on level:
          - PATIENT: (user_id, record_type_name, patient_id)
          - STUDY:   (user_id, record_type_name, study_uid)
          - SERIES:  (user_id, record_type_name, series_uid)

        Args:
            user_id: User UUID to check.
            record_type_name: RecordType name.
            patient_id: Patient identifier.
            study_uid: Study UID (used for STUDY/SERIES level).
            series_uid: Series UID (used for SERIES level).
            level: DicomQueryLevel value as string.

        Returns:
            Count of matching records regardless of status.
        """
        query = select(func.count(col(Record.id))).where(
            Record.record_type_name == record_type_name,
            col(Record.user_id) == user_id,
        )
        match level:
            case "PATIENT":
                query = query.where(Record.patient_id == patient_id)
            case "STUDY":
                query = query.where(Record.study_uid == study_uid)
            case "SERIES":
                query = query.where(Record.series_uid == series_uid)
            case _:
                raise ValueError(f"Unsupported level for unique_per_user context: {level}")
        result = await self.session.execute(query)
        return result.scalar_one()

    async def get_record_type(self, name: str, *, with_files: bool = True) -> RecordType:
        """Get a RecordType by name with file_links eagerly loaded.

        Args:
            name: Record type name (primary key)
            with_files: When False, resolve via ``session.get`` — an
                identity-map hit (no extra SQL) if the type was already
                loaded earlier in the request. ``file_links`` are NOT
                eagerly loaded then; access only scalar fields.

        Returns:
            RecordType instance

        Raises:
            RecordTypeNotFoundError: If record type doesn't exist
        """
        if not with_files:
            record_type_by_pk = await self.session.get(RecordType, name)
            if record_type_by_pk is None:
                raise RecordTypeNotFoundError(name)
            return record_type_by_pk
        stmt = (
            select(RecordType)
            .where(RecordType.name == name)
            .options(
                selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition)  # type: ignore[arg-type]  # type: ignore[arg-type]
            )
        )
        result = await self.session.execute(stmt)
        record_type = result.scalars().first()
        if not record_type:
            raise RecordTypeNotFoundError(name)
        return record_type

    async def delete_records(self, record_ids: list[int], *, commit: bool = True) -> int:
        """Bulk-delete records by primary key in a single SQL statement.

        ``RecordFileLink`` rows are cleaned up by the database-level
        ``ondelete="CASCADE"`` FK. ``parent_record_id`` references are
        cleared by ``ondelete="SET NULL"``.

        This avoids ORM unit-of-work ordering pitfalls that apply when
        deleting self-referential rows via ``session.delete()`` without
        eagerly loaded ``child_records`` on every level.

        Args:
            record_ids: IDs to delete. Empty list is a no-op.
            commit: When ``False``, skip the final commit so the caller can
                keep the transaction (and any row locks) open. Default ``True``.

        Returns:
            Number of deleted rows.
        """
        if not record_ids:
            return 0
        stmt = sa_delete(Record).where(col(Record.id).in_(record_ids))
        result = await self.session.execute(stmt)
        if commit:
            await self.session.commit()
            # sse-capture: explicit emit, UoW-invisible (Core bulk DML).
            # Children whose parent_record_id is SET NULL by the FK are
            # intentionally not emitted as "updated" (minor: parent rarely
            # affects the UI; thin events keep the client cache eventually
            # consistent via TTL/refetch).
            emit_entity("record", "deleted", [str(i) for i in record_ids])
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def collect_descendants(self, root_id: int, *, for_update: bool = False) -> list[Record]:
        """Collect the root record and all its descendants in BFS order.

        Each returned Record has ``patient``, ``study``, ``series``,
        ``record_type.file_links`` and ``file_links`` eagerly loaded so that
        the caller can resolve OUTPUT file paths without extra queries.
        Siblings are ordered by ``Record.id`` so the result is reproducible.

        Args:
            root_id: ID of the root record.
            for_update: When ``True``, acquire row-level locks on the whole
                subtree (`SELECT ... FOR UPDATE`). The caller is responsible
                for holding the transaction open until it commits the
                subsequent mutation, otherwise locks are released prematurely.

        Returns:
            List starting with the root followed by descendants (BFS order).

        Raises:
            RecordNotFoundError: If the root doesn't exist.
        """
        root = await self.get_with_relations(root_id, lock=for_update)
        collected: list[Record] = [root]
        visited: set[int] = {root_id}
        frontier: list[int] = [root_id]

        while frontier:
            statement = (
                select(Record)
                .where(col(Record.parent_record_id).in_(frontier))
                .order_by(col(Record.id).asc())
                .options(
                    selectinload(Record.patient),  # type: ignore[arg-type]
                    selectinload(Record.study),  # type: ignore[arg-type]
                    selectinload(Record.series),  # type: ignore[arg-type]
                    _record_type_with_files(),
                    _record_file_links_eager_load(),
                )
            )
            if for_update:
                statement = statement.with_for_update()
            result = await self.session.execute(statement)
            new_ids: list[int] = []
            for child in result.scalars().all():
                if child.id is None or child.id in visited:
                    continue
                visited.add(child.id)
                collected.append(child)
                new_ids.append(child.id)
            frontier = new_ids

        return collected

    async def check_constraints(
        self,
        record_type_name: str,
        series_uid: str | None,
        study_uid: str | None,
        patient_id: str | None = None,
        user_id: UUID | None = None,
        parent_record_id: int | None = None,
    ) -> None:
        """Check if a new record can be created based on constraints.

        Validates max_records, unique_per_user and parent_required constraints.

        Args:
            record_type_name: Record type name.
            series_uid: Series UID.
            study_uid: Study UID.
            patient_id: Patient ID (required for unique_per_user check).
            user_id: User UUID (triggers unique_per_user check when set).
            parent_record_id: Proposed parent record id (used to enforce
                ``parent_required`` on the RecordType).

        Raises:
            RecordTypeNotFoundError: If record type doesn't exist.
            RecordConstraintViolationError: If any constraint is violated.
        """
        record_type = await self.get_record_type(record_type_name)
        level = record_type.level

        # Validate level-UID consistency before counting
        if level in ("STUDY", "SERIES") and not study_uid:
            raise RecordConstraintViolationError(f"Records of level {level} require study_uid")
        if level == "SERIES" and not series_uid:
            raise RecordConstraintViolationError("Records of level SERIES require series_uid")

        if record_type.parent_required and parent_record_id is None:
            raise RecordParentRequiredError(
                f"Record type '{record_type_name}' requires a parent record"
            )

        # `max_records=0` is the deprecation sentinel — blocks any new records
        # for retired RecordTypes while keeping the row in the registry.
        if record_type.max_records is not None:
            count = await self.count_by_type_and_context(
                record_type_name=record_type_name,
                patient_id=patient_id,
                study_uid=study_uid,
                series_uid=series_uid,
                level=level,
            )
            if count >= record_type.max_records:
                raise RecordLimitReachedError(
                    f"The maximum records limit ({count} of {record_type.max_records}) is reached"
                )

        # Check unique_per_user constraint
        if user_id is not None and patient_id is not None:
            await self.ensure_unique_per_user(
                record_type,
                user_id,
                patient_id=patient_id,
                study_uid=study_uid,
                series_uid=series_uid,
            )

    async def ensure_unique_per_user(
        self,
        record_type: RecordType,
        user_id: UUID,
        *,
        patient_id: str,
        study_uid: str | None,
        series_uid: str | None,
    ) -> None:
        """Raise if the user already has a record of this unique-per-user type.

        No-op when the type is not ``unique_per_user``. Also used by
        ``RecordService.create_record`` to re-check after parent ``user_id``
        inheritance, which happens after the route-level constraint check.

        Raises:
            RecordUniquePerUserError: If a record already exists for the user
                in the given DICOM context.
        """
        if not record_type.unique_per_user:
            return
        user_count = await self.count_user_records_for_context(
            user_id=user_id,
            record_type_name=record_type.name,
            patient_id=patient_id,
            study_uid=study_uid,
            series_uid=series_uid,
            level=record_type.level,
        )
        if user_count > 0:
            raise RecordUniquePerUserError(
                f"User already has a record of type '{record_type.name}' "
                f"for this {record_type.level.lower()} context"
            )

    @staticmethod
    def _apply_anon_uid_filter(
        statement: SelectOfScalar[Record],
        value: str | None,
        model: type,
        column: Any,
    ) -> SelectOfScalar[Record]:
        """Apply Null / * / exact match filter for anonymous UID columns."""
        match value:
            case None:
                return statement
            case "Null":
                return statement.join(model).where(column.is_(None))
            case "*":
                return statement.join(model).where(column.is_not(None))
            case _:
                return statement.join(model).where(column == value)

    @staticmethod
    def _apply_data_query_filters(
        statement: SelectOfScalar[Record],
        queries: list[RecordFindResult],
    ) -> SelectOfScalar[Record]:
        """Apply JSON data field comparison filters."""
        if not queries:
            return statement
        statement = statement.where(Record.data.is_not(None))  # type: ignore[union-attr]
        for query in queries:
            if query.comparison_operator is None:
                continue
            data_field = Record.data[query.result_name].as_string()  # type: ignore[index]
            op_fn = _COMPARISON_OPS.get(
                RecordFindResultComparisonOperator(query.comparison_operator)
            )
            if op_fn is None:
                raise ValidationError(
                    f"Unsupported comparison operator: {query.comparison_operator}"
                )
            statement = statement.where(op_fn(data_field.cast(query.sql_type), query.result_value))
        return statement

    def _build_criteria_query(self, criteria: RecordSearchCriteria) -> SelectOfScalar[Record]:
        """Build a filtered SELECT from criteria, WITHOUT ordering or pagination."""
        statement = (
            select(Record)
            .join(RecordType)
            .options(
                selectinload(Record.patient),  # type: ignore
                selectinload(Record.study),  # type: ignore
                selectinload(Record.series),  # type: ignore
                _record_type_with_files(),
                _record_file_links_eager_load(),
            )
        )

        # Patient filters
        if criteria.patient_id:
            # If the value matches the anon_id pattern AND the caller is a
            # non-superuser (role_names attached), route through the
            # patient_anon_id branch so non-superusers can filter by the
            # masked identifier they see in /records/filter-options.
            # Superusers always see real patient_ids in dropdowns and
            # tables, so their patient_id input is taken literally — this
            # prevents a real PatientID that happens to start with
            # ``{anon_id_prefix}_`` from being silently rerouted.
            # The auto-route is also skipped when patient_anon_id is
            # already set: the explicit branch below would re-join
            # Patient and SQLAlchemy would error with "ambiguous column".
            anon_prefix = f"{settings.anon_id_prefix}_"
            use_auto_route = (
                criteria.patient_id.startswith(anon_prefix)
                and criteria.role_names is not None
                and not criteria.patient_anon_id
            )
            if use_auto_route:
                suffix = criteria.patient_id[len(anon_prefix) :]
                try:
                    auto_id = int(suffix)
                except ValueError:
                    auto_id = -1
                statement = statement.join(
                    Patient, col(Record.patient_id) == col(Patient.id)
                ).where(Patient.auto_id == auto_id)
            else:
                statement = statement.where(Record.patient_id == criteria.patient_id)

        if criteria.patient_anon_id:
            _, _, suffix = criteria.patient_anon_id.rpartition("_")
            try:
                auto_id = int(suffix)
            except ValueError:
                auto_id = -1  # invalid format — no results will match
            statement = statement.join(Patient, col(Record.patient_id) == col(Patient.id)).where(
                Patient.auto_id == auto_id
            )

        # Series filters
        if criteria.series_uid:
            statement = statement.where(Record.series_uid == criteria.series_uid)

        statement = self._apply_anon_uid_filter(
            statement, criteria.anon_series_uid, Series, col(Series.anon_uid)
        )

        # Study filters
        if criteria.study_uid:
            statement = statement.where(Record.study_uid == criteria.study_uid)

        statement = self._apply_anon_uid_filter(
            statement, criteria.anon_study_uid, Study, col(Study.anon_uid)
        )

        # User filters
        if criteria.wo_user is True:
            statement = statement.where(col(Record.user_id).is_(None))
        elif criteria.wo_user is False:
            statement = statement.where(col(Record.user_id).is_not(None))

        if criteria.user_id:
            if criteria.include_unassigned:
                statement = statement.where(
                    or_(col(Record.user_id) == criteria.user_id, col(Record.user_id).is_(None))
                )
            else:
                statement = statement.where(Record.user_id == criteria.user_id)

        if criteria.exclude_unique_violations and criteria.user_id:
            statement = statement.where(_unique_per_user_violation_filter(criteria.user_id))

        # Record filters
        if criteria.record_status:
            statement = statement.where(Record.status == criteria.record_status)

        if criteria.record_type_name:
            statement = statement.where(RecordType.name == criteria.record_type_name)

        # Parent record filter
        if criteria.parent_record_id is not None:
            statement = statement.where(Record.parent_record_id == criteria.parent_record_id)

        # Role-based access filter
        if criteria.role_names is not None:
            statement = statement.where(col(RecordType.role_name).in_(list(criteria.role_names)))

        # Data filters
        statement = self._apply_data_query_filters(statement, criteria.data_queries)

        return statement

    async def find_by_criteria(
        self,
        criteria: RecordSearchCriteria,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Record]:
        """Find records by various criteria with all relations loaded."""
        statement = self._build_criteria_query(criteria)

        # Pagination — all joins are N:1, so no duplicates possible (.distinct() removed
        # because PostgreSQL cannot compare JSON columns needed by Record.data).
        statement = self._paginate(statement, skip, limit)

        result = await self.session.execute(statement)
        results = list(result.scalars().all())

        if criteria.random_one and results:
            results = [random.choice(results)]

        logger.info(f"Found {len(results)} records matching criteria")
        return results

    async def find_random(
        self,
        criteria: RecordSearchCriteria,
        *,
        for_update: bool = False,
    ) -> Record | None:
        """Find a single random record matching criteria (SQL-level random).

        ``for_update=True`` locks the chosen row with ``FOR UPDATE OF record
        SKIP LOCKED`` (PostgreSQL) so a concurrent claimer skips it instead of
        selecting the same record — this is what makes the claim-from-pool
        select-then-claim atomic. SQLite omits the clause (no row locking),
        which only relaxes the in-memory test runner, never production.
        """
        statement = self._build_criteria_query(criteria)
        statement = statement.order_by(func.random()).limit(1)
        if for_update:
            statement = statement.with_for_update(skip_locked=True, of=Record)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def find_page(
        self,
        criteria: RecordSearchCriteria,
        *,
        cursor: str | None,
        limit: int,
        sort: SortOrder = "changed_at_desc",
    ) -> RecordPageResult:
        """Find records with keyset cursor pagination."""
        if criteria.random_one:
            raise ValidationError("random_one is incompatible with cursor pagination")

        statement = self._build_criteria_query(criteria)
        sort_spec = _SORT_SPECS[sort]

        # Optional join for modality sort (Series may not be filtered elsewhere)
        series_sort_alias: Any = None
        if sort_spec.kind is _SortColumnKind.MODALITY_ALIAS:
            series_sort_alias = aliased(Series)
            statement = statement.outerjoin(
                series_sort_alias,
                col(Record.series_uid) == col(series_sort_alias.series_uid),
            )

        sort_col = sort_spec.column(series_sort_alias)
        ascending = sort_spec.ascending

        # Sort order: primary by sort column (NULLS LAST for nullable), tie-break by id.
        if sort == "changed_at_desc":
            # Preserve legacy ordering: changed_at DESC, id DESC (no NULLS handling
            # because changed_at is non-nullable for any existing record).
            statement = statement.order_by(col(Record.changed_at).desc(), col(Record.id).desc())
        elif sort_col is None:
            # id_asc / id_desc — single-column order.
            statement = statement.order_by(
                col(Record.id).asc() if ascending else col(Record.id).desc()
            )
        else:
            order_expr = sort_col.asc() if ascending else sort_col.desc()
            if sort_spec.nullable:
                order_expr = order_expr.nulls_last()
            statement = statement.order_by(order_expr, col(Record.id).asc())

        # Keyset WHERE from cursor. `decode_cursor` already validates the
        # version + sort-order tags, but the payload values themselves
        # (`data["k"]` for the column, `data["i"]` for the id) feed into
        # `RecordStatus(...)` / `UUID(...)` / `datetime.fromisoformat(...)`
        # inside `_keyset_where`. A tampered cursor that keeps the right
        # outer envelope but ships a malformed inner value would surface
        # as a 500 — wrap once here and translate the failure into
        # `InvalidCursorError` (handled by the exception layer as 422).
        if cursor:
            data = decode_cursor(cursor, sort)
            try:
                cursor_id = int(data["i"])
            except (KeyError, TypeError, ValueError) as exc:
                raise InvalidCursorError(f"Cursor missing/invalid row id: {exc}") from exc
            try:
                where_clause = _keyset_where(
                    sort=sort,
                    sort_col=sort_col,
                    cursor_key=data.get("k"),
                    cursor_id=cursor_id,
                )
            except InvalidCursorError:
                raise
            except (ValueError, TypeError) as exc:
                raise InvalidCursorError(
                    f"Cursor key incompatible with sort '{sort}': {exc}"
                ) from exc
            statement = statement.where(where_clause)

        # Fetch limit+1 to detect next page
        statement = statement.limit(limit + 1)
        result = await self.session.execute(statement)
        records = list(result.scalars().all())

        if len(records) > limit:
            records = records[:limit]
            last = records[-1]
            next_cursor = encode_cursor(sort, sort_spec.extract_key(last), last.id)  # type: ignore[arg-type]
        else:
            next_cursor = None

        return RecordPageResult(records=records, next_cursor=next_cursor)

    async def get_status_counts(self) -> dict[str, int]:
        """Get record counts grouped by status.

        Returns:
            Dict mapping status value to count, e.g. {"pending": 5, "inwork": 3}
        """
        query = select(col(Record.status), func.count()).group_by(col(Record.status))
        result = await self.session.execute(query)
        return {status.value: count for status, count in result.all()}

    async def get_per_type_status_counts(self) -> dict[str, dict[str, int]]:
        """Get per-record-type, per-status counts.

        Returns:
            Nested dict: {type_name: {status_value: count}}
        """
        query = select(
            col(Record.record_type_name),
            col(Record.status),
            func.count(col(Record.id)),
        ).group_by(col(Record.record_type_name), col(Record.status))
        result = await self.session.execute(query)

        status_map: dict[str, dict[str, int]] = {}
        for type_name, status, count in result.all():
            status_map.setdefault(type_name, {})[status.value] = count
        return status_map

    async def get_per_type_unique_users(self) -> dict[str, int]:
        """Get unique assigned user count per record type.

        Returns:
            Dict mapping type_name to unique user count
        """
        query = (
            select(
                col(Record.record_type_name),
                func.count(distinct(col(Record.user_id))),
            )
            .where(col(Record.user_id).is_not(None))
            .group_by(col(Record.record_type_name))
        )
        result = await self.session.execute(query)
        rows = result.all()
        return {type_name: count for type_name, count in rows}  # noqa: C416

    async def get_filter_options(
        self, criteria: RecordSearchCriteria, user: User
    ) -> RecordFilterScope:
        """Get distinct patient/record_type/user values within criteria scope.

        Used to populate filter dropdowns on /records and /admin. Caller
        should leave user-driven UI filters (patient_id, record_type_name,
        user_id, wo_user, ...) at their defaults — only the RBAC fields
        (role_names, include_unassigned, exclude_unique_violations) should
        be set, so the response reflects the user's full accessible scope.

        For non-superusers, patient_ids of anonymized patients (``anon_name``
        set) are replaced with their ``anon_id`` — same masking guarantee
        as ``mask_record_patient_data``. The per-RecordType
        ``mask_patient_data=False`` opt-out is deliberately NOT honored
        here, because the dropdown aggregates across record types and the
        safest default is to never leak a real PatientID through a
        filter-options surface. ``_build_criteria_query`` accepts anon_id
        values in the ``patient_id`` filter and auto-routes them through
        the anon branch, so the user's filter submission still resolves.
        """
        # ``with_only_columns`` rebuilds the projection so the
        # selectinload options attached by ``_build_criteria_query``
        # become inert; the resulting subquery is a thin SELECT of the
        # three columns we actually distinct/group on below.
        subq = (
            self._build_criteria_query(criteria)
            .with_only_columns(
                col(Record.patient_id),
                col(Record.record_type_name),
                col(Record.user_id),
            )
            .subquery()
        )

        patients_result = await self.session.execute(
            select(distinct(subq.c.patient_id)).where(subq.c.patient_id.is_not(None))
        )
        types_result = await self.session.execute(
            select(distinct(subq.c.record_type_name)).where(subq.c.record_type_name.is_not(None))
        )
        users_result = await self.session.execute(
            select(distinct(subq.c.user_id)).where(subq.c.user_id.is_not(None))
        )
        unassigned_result = await self.session.execute(
            select(func.count()).select_from(subq).where(subq.c.user_id.is_(None))
        )

        raw_patient_ids = [str(p) for p in patients_result.scalars().all()]
        patients = await self._mask_patient_ids(raw_patient_ids, user)

        return RecordFilterScope(
            patients=patients,
            record_types=sorted(t for t in types_result.scalars().all()),
            users=sorted(str(u) for u in users_result.scalars().all()),
            has_unassigned=(unassigned_result.scalar() or 0) > 0,
        )

    async def _mask_patient_ids(self, patient_ids: list[str], user: User) -> list[str]:
        """Replace real patient_ids with anon_ids for non-superusers.

        Sorted output. Superusers pass through unchanged. Patients without
        ``anon_name`` keep their real id (not anonymized yet, nothing to
        mask).

        When ``settings.anon_per_study_patient_id`` is enabled the row
        layer masks each ``patient_id`` to a per-study hash, so a flat
        ``anon_id``-based dropdown would not match what the user sees in
        the table. In that mode anonymized patients are dropped from the
        dropdown entirely (non-superusers can still filter by patients
        not yet anonymized).
        """
        if user.is_superuser or not patient_ids:
            return sorted(patient_ids)

        result = await self.session.execute(
            select(Patient.id, Patient.anon_name, Patient.auto_id).where(
                col(Patient.id).in_(patient_ids)
            )
        )
        per_study_mode = settings.anon_per_study_patient_id
        # None ⇒ hide this patient from the dropdown
        anon_map: dict[str, str | None] = {}
        for pid, anon_name, auto_id in result.all():
            if anon_name is None or auto_id is None:
                continue
            anon_map[pid] = None if per_study_mode else f"{settings.anon_id_prefix}_{auto_id}"

        masked: set[str] = set()
        collisions: list[str] = []
        for pid in patient_ids:
            mapped = anon_map.get(pid, pid)
            if mapped is None:
                continue
            if mapped in masked:
                collisions.append(pid)
            masked.add(mapped)
        if collisions:
            logger.warning(
                f"filter-options: distinct patient_ids collapsed after masking "
                f"({len(collisions)} duplicate(s)); likely a real PatientID "
                f"collides with anon_id_prefix={settings.anon_id_prefix!r}"
            )
        return sorted(masked)

    async def get_available_type_counts(
        self,
        user_id: UUID,
        exclude_unique_violations: bool = False,
    ) -> dict[RecordType, int]:
        """Get record types with pending record counts available to a user.

        Args:
            user_id: User UUID
            exclude_unique_violations: If True, exclude unassigned records that
                violate unique_per_user for this user

        Returns:
            Dict mapping RecordType to count of pending records
        """
        # Get user's role names first, then filter record types
        role_result = await self.session.execute(
            select(col(UserRolesLink.role_name)).where(UserRolesLink.user_id == user_id)
        )
        role_names = set(role_result.scalars().all())
        if not role_names:
            return {}

        statement = (
            select(RecordType.name, func.count(col(Record.id)).label("record_count"))
            .join(Record)
            .where(col(RecordType.role_name).in_(list(role_names)))
            .where(Record.status == RecordStatus.pending)
        )
        if exclude_unique_violations:
            statement = statement.where(_unique_per_user_violation_filter(user_id))
        statement = statement.group_by(col(RecordType.name))
        result = await self.session.execute(statement)
        rows = result.all()

        if not rows:
            return {}

        # Batch fetch RecordTypes with file_links to avoid N+1
        names = [name for name, _ in rows]
        types_result = await self.session.execute(
            select(RecordType)
            .where(col(RecordType.name).in_(names))
            .options(
                selectinload(RecordType.file_links).selectinload(RecordTypeFileLink.file_definition)  # type: ignore[arg-type]  # type: ignore[arg-type]
            )
        )
        type_map = {rt.name: rt for rt in types_result.scalars().all()}

        return {type_map[name]: count for name, count in rows if name in type_map}
