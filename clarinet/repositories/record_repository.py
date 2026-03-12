"""Repository for Record-specific database operations."""

import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import distinct, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col, select

from clarinet.exceptions.domain import (
    RecordConstraintViolationError,
    RecordNotFoundError,
    RecordTypeNotFoundError,
    UserNotFoundError,
    ValidationError,
)
from clarinet.models import Record
from clarinet.models.base import RecordStatus
from clarinet.models.file_schema import RecordFileLink, RecordTypeFileLink
from clarinet.models.patient import Patient
from clarinet.models.record import RecordFindResult, RecordFindResultComparisonOperator, RecordType
from clarinet.models.study import Series, Study
from clarinet.models.user import User, UserRole
from clarinet.repositories.base import BaseRepository
from clarinet.types import RecordData
from clarinet.utils.logger import logger


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
    random_one: bool = False
    role_names: set[str] | None = None
    data_queries: list[RecordFindResult] = field(default_factory=list)


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

    async def get_with_relations(self, record_id: int) -> Record:
        """Get a single record with all relationships eagerly loaded.

        Args:
            record_id: Record ID

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
        result = await self.session.execute(statement)
        record = result.scalars().first()
        if not record:
            raise RecordNotFoundError(record_id)
        return record

    async def get_all_with_relations(self, skip: int = 0, limit: int = 100) -> Sequence[Record]:
        """Get all records with all relationships eagerly loaded.

        Args:
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of records with patient, study, series, and record_type loaded
        """
        statement = (
            select(Record)
            .options(
                selectinload(Record.patient),  # type: ignore
                selectinload(Record.study),  # type: ignore
                selectinload(Record.series),  # type: ignore
                _record_type_with_files(),
                _record_file_links_eager_load(),
            )
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_all_for_user_roles(
        self, role_names: set[str], skip: int = 0, limit: int = 100
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
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def find_by_user(
        self,
        user_id: UUID,
        skip: int = 0,
        limit: int = 100,
        role_names: set[str] | None = None,
        include_unassigned: bool = False,
    ) -> Sequence[Record]:
        """Find records assigned to a specific user with relations loaded.

        Args:
            user_id: User UUID to filter by
            skip: Number of records to skip
            limit: Maximum number of records to return
            role_names: Optional set of role names to filter by
            include_unassigned: If True, also include records with user_id=NULL

        Returns:
            List of records with patient, study, series, and record_type loaded
        """
        user_filter = (
            or_(col(Record.user_id) == user_id, col(Record.user_id).is_(None))
            if include_unassigned
            else col(Record.user_id) == user_id
        )
        statement = (
            select(Record)
            .where(user_filter)
            .options(
                selectinload(Record.patient),  # type: ignore
                selectinload(Record.study),  # type: ignore
                selectinload(Record.series),  # type: ignore
                _record_type_with_files(),
                _record_file_links_eager_load(),
            )
            .offset(skip)
            .limit(limit)
        )
        if role_names is not None:
            statement = statement.join(RecordType).where(
                col(RecordType.role_name).in_(list(role_names))
            )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def find_pending_by_user(
        self,
        user_id: UUID,
        role_names: set[str] | None = None,
        include_unassigned: bool = False,
    ) -> Sequence[Record]:
        """Find active (non-terminal) records assigned to a user with relations loaded.

        Returns records that are not blocked, finished, failed, or paused.

        Args:
            user_id: User UUID to filter by
            role_names: Optional set of role names to filter by
            include_unassigned: If True, also include records with user_id=NULL

        Returns:
            List of active records with patient, study, series, and record_type loaded
        """
        user_filter = (
            or_(col(Record.user_id) == user_id, col(Record.user_id).is_(None))
            if include_unassigned
            else col(Record.user_id) == user_id
        )
        statement = (
            select(Record)
            .where(
                user_filter,
                Record.status != RecordStatus.blocked,
                Record.status != RecordStatus.finished,
                Record.status != RecordStatus.failed,
                Record.status != RecordStatus.pause,
            )
            .options(
                selectinload(Record.patient),  # type: ignore
                selectinload(Record.study),  # type: ignore
                selectinload(Record.series),  # type: ignore
                _record_type_with_files(),
                _record_file_links_eager_load(),
            )
        )
        if role_names is not None:
            statement = statement.join(RecordType).where(
                col(RecordType.role_name).in_(list(role_names))
            )
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
            checksums: New checksums dict (file definition name -> SHA256)
        """
        for link in record.file_links or []:
            if link.file_definition.name in checksums:
                link.checksum = checksums[link.file_definition.name]
        await self.session.commit()

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
                record_id=record.id,  # type: ignore[arg-type]
                file_definition_id=fd.id,  # type: ignore[arg-type]
                filename=filename,
            )
            self.session.add(link)
        await self.session.commit()

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
            ValidationError: If record is blocked
        """
        record = await self.get(record_id)
        if record.status == RecordStatus.blocked:
            raise ValidationError("Cannot assign user to a blocked record")
        user = await self.session.get(User, user_id)
        if not user:
            raise UserNotFoundError(user_id)
        old_status = record.status
        record.user_id = user_id
        record.status = RecordStatus.inwork
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
        """
        record = await self.get(record_id)
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
            mode: "hard" resets status to pending (keeps user_id).
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

        if mode == "hard":
            record.status = RecordStatus.pending

        await self.session.commit()
        return await self.get_with_relations(record_id)

    async def count_by_type_and_context(
        self,
        record_type_name: str,
        series_uid: str | None,
        study_uid: str | None,
    ) -> int:
        """Count records matching type and study/series context.

        Args:
            record_type_name: Record type name to filter by
            series_uid: Series UID to filter by
            study_uid: Study UID to filter by

        Returns:
            Number of matching records
        """
        query = (
            select(func.count(col(Record.id)))
            .join(RecordType)
            .where(
                RecordType.name == record_type_name,
                Record.series_uid == series_uid,
                Record.study_uid == study_uid,
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one()

    async def get_record_type(self, name: str) -> RecordType:
        """Get a RecordType by name with file_links eagerly loaded.

        Args:
            name: Record type name (primary key)

        Returns:
            RecordType instance

        Raises:
            RecordTypeNotFoundError: If record type doesn't exist
        """
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

    async def validate_parent_record(self, parent_record_id: int, child_type_name: str) -> Record:
        """Validate parent_record_id is valid for this child type.

        Args:
            parent_record_id: ID of the proposed parent record.
            child_type_name: Name of the child RecordType.

        Returns:
            Parent record (for user_id inheritance).

        Raises:
            RecordNotFoundError: If parent record doesn't exist.
            ValidationError: If parent record type doesn't match child's parent_type_name.
        """
        parent = await self.get(parent_record_id)
        child_type = await self.get_record_type(child_type_name)

        if child_type.parent_type_name is None:
            raise ValidationError(
                f"RecordType '{child_type_name}' does not define a parent_type_name"
            )

        if parent.record_type_name != child_type.parent_type_name:
            raise ValidationError(
                f"Parent record type '{parent.record_type_name}' does not match "
                f"expected parent type '{child_type.parent_type_name}' "
                f"for child type '{child_type_name}'"
            )

        return parent

    async def check_constraints(
        self,
        record_type_name: str,
        series_uid: str | None,
        study_uid: str | None,
    ) -> None:
        """Check if a new record can be created based on max_records constraint.

        Args:
            record_type_name: Record type name
            series_uid: Series UID
            study_uid: Study UID

        Raises:
            RecordTypeNotFoundError: If record type doesn't exist
            RecordConstraintViolationError: If constraint is violated
        """
        count = await self.count_by_type_and_context(record_type_name, series_uid, study_uid)
        record_type = await self.get_record_type(record_type_name)

        if record_type.max_records and count >= record_type.max_records:
            raise RecordConstraintViolationError(
                f"The maximum records limit ({count} of {record_type.max_records}) is reached"
            )

        # Validate level-UID consistency
        level = record_type.level
        if level in ("STUDY", "SERIES") and not study_uid:
            raise RecordConstraintViolationError(f"Records of level {level} require study_uid")
        if level == "SERIES" and not series_uid:
            raise RecordConstraintViolationError("Records of level SERIES require series_uid")

    @staticmethod
    def _apply_anon_uid_filter(
        statement: Any,
        value: str | None,
        model: type,
        column: Any,
    ) -> Any:
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
        statement: Any,
        queries: list[RecordFindResult],
    ) -> Any:
        """Apply JSON data field comparison filters."""
        for query in queries:
            if query.comparison_operator is None:
                continue
            data_field = Record.data.op("->")(query.result_name).as_string()  # type: ignore[union-attr]
            op_fn = _COMPARISON_OPS.get(query.comparison_operator)
            if op_fn is None:
                raise ValidationError(
                    f"Unsupported comparison operator: {query.comparison_operator}"
                )
            statement = statement.where(op_fn(data_field.cast(query.sql_type), query.result_value))
        return statement

    async def find_by_criteria(
        self,
        criteria: RecordSearchCriteria,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Record]:
        """Find records by various criteria with all relations loaded.

        Args:
            criteria: Search criteria
            skip: Number of records to skip
            limit: Maximum number of records to return

        Returns:
            List of matching records with relations loaded
        """
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
            statement = statement.join(Study).join(Patient).where(Patient.id == criteria.patient_id)

        if criteria.patient_anon_id and "_" in criteria.patient_anon_id:
            auto_id = int(criteria.patient_anon_id.split("_")[1])
            statement = statement.join(Study).join(Patient).where(Patient.auto_id == auto_id)

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
            statement = statement.where(Record.user_id == criteria.user_id)

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

        # Pagination
        statement = statement.distinct().offset(skip).limit(limit)

        result = await self.session.execute(statement)
        results = list(result.scalars().all())

        if criteria.random_one and results:
            results = [random.choice(results)]

        logger.info(f"Found {len(results)} records matching criteria")
        return results

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

    async def get_available_type_counts(self, user_id: UUID) -> dict[RecordType, int]:
        """Get record types with pending record counts available to a user.

        Args:
            user_id: User UUID

        Returns:
            Dict mapping RecordType to count of pending records
        """
        statement = (
            select(RecordType.name, func.count(col(Record.id)).label("record_count"))
            .join(Record)
            .join(UserRole)
            .where(UserRole.users.any(User.id == user_id))  # type: ignore[attr-defined]
            .where(Record.status == RecordStatus.pending)
            .group_by(col(RecordType.name))
        )
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
