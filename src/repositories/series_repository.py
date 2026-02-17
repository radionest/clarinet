"""Repository for Series-specific database operations."""

from collections.abc import Sequence

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from src.models import Record, RecordType, Series
from src.models.study import SeriesFind
from src.repositories.base import BaseRepository


class SeriesRepository(BaseRepository[Series]):
    """Repository for Series model operations."""

    def __init__(self, session: AsyncSession):
        """Initialize series repository with session."""
        super().__init__(session, Series)

    async def get_with_relations(self, series_id: str) -> Series:
        """Get series with relations loaded.

        Args:
            series_id: Series ID

        Returns:
            Series with relations loaded

        Raises:
            NOT_FOUND: If series doesn't exist
        """
        statement = (
            select(Series)
            .where(Series.series_uid == series_id)
            .options(selectinload(Series.study), selectinload(Series.records))  # type: ignore
        )
        result = await self.session.execute(statement)
        series = result.scalars().first()

        if not series:
            from src.exceptions import NOT_FOUND

            raise NOT_FOUND.with_context(f"Series {series_id} not found")

        return series

    async def find_by_study_uid(
        self, study_uid: str, skip: int = 0, limit: int = 100
    ) -> Sequence[Series]:
        """Find all series for a study.

        Args:
            study_uid: Study UID
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of series
        """
        statement = select(Series).where(Series.study_uid == study_uid).offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def find_by_anon_uid(self, anon_uid: str) -> Series | None:
        """Find series by anonymous UID.

        Args:
            anon_uid: Anonymous UID

        Returns:
            Series if found, None otherwise
        """
        statement = select(Series).where(Series.anon_uid == anon_uid)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def find_by_series_uid(self, series_uid: str) -> Series | None:
        """Find series by series instance UID.

        Args:
            series_uid: Series instance UID

        Returns:
            Series if found, None otherwise
        """
        statement = select(Series).where(Series.series_uid == series_uid)
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def find_by_description(
        self,
        description: str,
        study_uid: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Series]:
        """Find series by description.

        Args:
            description: Description to search for
            study_uid: Optional study UID filter
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of series
        """
        statement = select(Series)

        if description:
            statement = statement.where(Series.series_description.ilike(f"%{description}%"))  # type: ignore

        if study_uid:
            statement = statement.where(Series.study_uid == study_uid)

        statement = statement.offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_records(self, series_id: str) -> list[Record]:
        """Get all records for a series.

        Args:
            series_id: Series ID

        Returns:
            List of records
        """
        series = await self.get(series_id)
        await self.session.refresh(series, ["records"])
        return list(series.records)

    async def count_by_study_uid(self, study_uid: str) -> int:
        """Count series for a study.

        Args:
            study_uid: Study UID

        Returns:
            Number of series
        """
        statement = select(func.count()).select_from(Series).where(Series.study_uid == study_uid)
        result = await self.session.execute(statement)
        return result.scalar() or 0

    async def count_records(self, series_uid: str) -> int:
        """Count records for a series.

        Args:
            series_uid: Series UID

        Returns:
            Number of records
        """
        statement = select(func.count()).select_from(Record).where(Record.series_uid == series_uid)
        result = await self.session.execute(statement)
        return result.scalar() or 0

    async def search(
        self,
        study_uid: str | None = None,
        series_uid: str | None = None,
        series_description: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[Series]:
        """Search series with multiple filters.

        Args:
            study_uid: Filter by study UID
            series_uid: Filter by series UID
            series_description: Search in series description
            skip: Number of records to skip
            limit: Maximum number of records

        Returns:
            List of matching series
        """
        statement = select(Series)

        if study_uid:
            statement = statement.where(Series.study_uid == study_uid)

        if series_uid:
            statement = statement.where(Series.series_uid == series_uid)

        if series_description:
            statement = statement.where(Series.series_description.ilike(f"%{series_description}%"))  # type: ignore

        statement = statement.offset(skip).limit(limit)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_random_series(self, study_uid: str, count: int = 1) -> list[Series]:
        """Get random series from a study.

        Args:
            study_uid: Study UID
            count: Number of random series to get

        Returns:
            List of random series
        """
        statement = select(Series).where(Series.study_uid == study_uid)

        # Use random ordering
        statement = statement.order_by(func.random()).limit(count)

        result = await self.session.execute(statement)
        return list(result.scalars().all())

    async def update_anon_uid(self, series: Series, anon_uid: str) -> Series:
        """Update series anonymous UID.

        Args:
            series: Series to update
            anon_uid: New anonymous UID

        Returns:
            Updated series
        """
        series.anon_uid = anon_uid
        await self.session.commit()
        await self.session.refresh(series)
        return series

    async def get_random(self) -> Series:
        """Get a random series from all series.

        Returns:
            Random series

        Raises:
            NOT_FOUND: If no series exist
        """
        statement = select(Series).order_by(func.random()).limit(1)
        result = await self.session.execute(statement)
        series = result.scalars().first()

        if not series:
            from src.exceptions import NOT_FOUND

            raise NOT_FOUND.with_context("No series found")

        return series

    async def find_by_criteria(self, find_query: SeriesFind) -> list[Series]:
        """Find series by criteria.

        Args:
            find_query: Search criteria

        Returns:
            List of matching series
        """
        statement = select(Series)

        for query_key, query_value in find_query.model_dump(
            exclude_none=True, exclude_defaults=True, exclude={"records"}
        ).items():
            if hasattr(Series, query_key):
                if query_value == "*":
                    statement = statement.where(getattr(Series, query_key).isnot(None))
                else:
                    statement = statement.where(getattr(Series, query_key) == query_value)

        if find_query.records:
            statement = statement.join(Record, isouter=True)
            statement = statement.join(RecordType, isouter=True)

        result = await self.session.execute(statement.distinct())
        return list(result.scalars().all())
