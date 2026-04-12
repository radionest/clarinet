"""Integration tests for cursor-based keyset pagination (find_page)."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.repositories.record_repository import (
    RecordRepository,
    RecordSearchCriteria,
)
from clarinet.utils.pagination import InvalidCursorError
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    seed_record,
)
from tests.utils.urls import RECORDS_FIND_RANDOM


@pytest_asyncio.fixture
async def page_env(test_session: AsyncSession):
    """Seed 8 records with distinct changed_at timestamps."""
    pat = make_patient("PAGE_PAT")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("PAGE_PAT", "1.2.3.900")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.900", "1.2.3.900.1")
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("page-test-rt")
    test_session.add(rt)
    await test_session.commit()

    base_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    records = []
    for i in range(8):
        rec = await seed_record(
            test_session,
            patient_id="PAGE_PAT",
            study_uid="1.2.3.900",
            series_uid="1.2.3.900.1",
            rt_name="page-test-rt",
            changed_at=base_time + timedelta(hours=i),
        )
        records.append(rec)

    repo = RecordRepository(test_session)
    return {
        "repo": repo,
        "records": records,
        "criteria": RecordSearchCriteria(patient_id="PAGE_PAT"),
    }


class TestFindPageBasic:
    @pytest.mark.asyncio
    async def test_first_page(self, page_env):
        result = await page_env["repo"].find_page(
            page_env["criteria"], cursor=None, limit=3, sort="changed_at_desc"
        )
        assert len(result.records) == 3
        assert result.next_cursor is not None
        # changed_at_desc: newest first
        ids = [r.id for r in result.records]
        assert ids == sorted(ids, reverse=True)

    @pytest.mark.asyncio
    async def test_second_page_via_cursor(self, page_env):
        first = await page_env["repo"].find_page(
            page_env["criteria"], cursor=None, limit=3, sort="changed_at_desc"
        )
        second = await page_env["repo"].find_page(
            page_env["criteria"], cursor=first.next_cursor, limit=3, sort="changed_at_desc"
        )
        assert len(second.records) == 3
        assert second.next_cursor is not None
        # No overlap between pages
        first_ids = {r.id for r in first.records}
        second_ids = {r.id for r in second.records}
        assert first_ids.isdisjoint(second_ids)

    @pytest.mark.asyncio
    async def test_last_page_no_cursor(self, page_env):
        # Fetch all 8 records in pages of 3: 3+3+2
        first = await page_env["repo"].find_page(
            page_env["criteria"], cursor=None, limit=3, sort="changed_at_desc"
        )
        second = await page_env["repo"].find_page(
            page_env["criteria"], cursor=first.next_cursor, limit=3, sort="changed_at_desc"
        )
        third = await page_env["repo"].find_page(
            page_env["criteria"], cursor=second.next_cursor, limit=3, sort="changed_at_desc"
        )
        assert len(third.records) == 2
        assert third.next_cursor is None

    @pytest.mark.asyncio
    async def test_all_records_covered(self, page_env):
        """Iterating through all pages yields all 8 records without duplicates."""
        all_ids: list[int] = []
        cursor = None
        for _ in range(10):  # safety limit
            result = await page_env["repo"].find_page(
                page_env["criteria"], cursor=cursor, limit=3, sort="changed_at_desc"
            )
            all_ids.extend(r.id for r in result.records)
            if result.next_cursor is None:
                break
            cursor = result.next_cursor
        assert len(all_ids) == 8
        assert len(set(all_ids)) == 8


class TestFindPageSortOrders:
    @pytest.mark.asyncio
    async def test_id_asc(self, page_env):
        result = await page_env["repo"].find_page(
            page_env["criteria"], cursor=None, limit=4, sort="id_asc"
        )
        ids = [r.id for r in result.records]
        assert ids == sorted(ids)
        assert result.next_cursor is not None

    @pytest.mark.asyncio
    async def test_id_desc(self, page_env):
        result = await page_env["repo"].find_page(
            page_env["criteria"], cursor=None, limit=4, sort="id_desc"
        )
        ids = [r.id for r in result.records]
        assert ids == sorted(ids, reverse=True)

    @pytest.mark.asyncio
    async def test_id_asc_full_iteration(self, page_env):
        all_ids: list[int] = []
        cursor = None
        for _ in range(10):
            result = await page_env["repo"].find_page(
                page_env["criteria"], cursor=cursor, limit=3, sort="id_asc"
            )
            all_ids.extend(r.id for r in result.records)
            if result.next_cursor is None:
                break
            cursor = result.next_cursor
        assert all_ids == sorted(all_ids)
        assert len(all_ids) == 8


class TestFindPageCursorErrors:
    @pytest.mark.asyncio
    async def test_invalid_cursor_raises(self, page_env):
        with pytest.raises(InvalidCursorError):
            await page_env["repo"].find_page(
                page_env["criteria"], cursor="bad-cursor", limit=3, sort="changed_at_desc"
            )

    @pytest.mark.asyncio
    async def test_sort_mismatch_raises(self, page_env):
        first = await page_env["repo"].find_page(
            page_env["criteria"], cursor=None, limit=3, sort="changed_at_desc"
        )
        with pytest.raises(InvalidCursorError, match="does not match"):
            await page_env["repo"].find_page(
                page_env["criteria"], cursor=first.next_cursor, limit=3, sort="id_asc"
            )


class TestFindPageEdgeCases:
    @pytest.mark.asyncio
    async def test_limit_larger_than_total(self, page_env):
        result = await page_env["repo"].find_page(
            page_env["criteria"], cursor=None, limit=100, sort="changed_at_desc"
        )
        assert len(result.records) == 8
        assert result.next_cursor is None

    @pytest.mark.asyncio
    async def test_empty_result(self, page_env):
        criteria = RecordSearchCriteria(patient_id="NONEXISTENT")
        result = await page_env["repo"].find_page(
            criteria, cursor=None, limit=10, sort="changed_at_desc"
        )
        assert len(result.records) == 0
        assert result.next_cursor is None

    @pytest.mark.asyncio
    async def test_random_one_incompatible(self, page_env):
        criteria = RecordSearchCriteria(patient_id="PAGE_PAT", random_one=True)
        with pytest.raises(Exception, match="random_one"):
            await page_env["repo"].find_page(criteria, cursor=None, limit=3, sort="changed_at_desc")


class TestFindRandomRecord:
    @pytest.mark.asyncio
    async def test_random_returns_one_record(self, page_env):
        criteria = RecordSearchCriteria(patient_id="PAGE_PAT", random_one=True)
        results = await page_env["repo"].find_by_criteria(criteria)
        assert len(results) == 1
        assert results[0].patient_id == "PAGE_PAT"

    @pytest.mark.asyncio
    async def test_random_no_match_returns_empty(self, page_env):
        criteria = RecordSearchCriteria(patient_id="NONEXISTENT", random_one=True)
        results = await page_env["repo"].find_by_criteria(criteria)
        assert len(results) == 0


class TestFindRandomEndpoint:
    @pytest.mark.asyncio
    async def test_returns_one_record(self, client, page_env):
        resp = await client.post(RECORDS_FIND_RANDOM, json={"patient_id": "PAGE_PAT"})
        assert resp.status_code == 200
        data = resp.json()
        assert data is not None
        assert data["patient_id"] == "PAGE_PAT"

    @pytest.mark.asyncio
    async def test_no_match_returns_null(self, client, page_env):
        resp = await client.post(RECORDS_FIND_RANDOM, json={"patient_id": "NONEXISTENT"})
        assert resp.status_code == 200
        assert resp.json() is None

    @pytest.mark.asyncio
    async def test_rejects_pagination_fields(self, client, page_env):
        resp = await client.post(
            RECORDS_FIND_RANDOM,
            json={"patient_id": "PAGE_PAT", "cursor": "abc"},
        )
        assert resp.status_code == 422
