"""Integration tests for cursor-based keyset pagination (find_page)."""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.record import RecordType
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
    make_user,
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
        criteria = RecordSearchCriteria(patient_id="PAGE_PAT")
        record = await page_env["repo"].find_random(criteria)
        assert record is not None
        assert record.patient_id == "PAGE_PAT"

    @pytest.mark.asyncio
    async def test_random_no_match_returns_none(self, page_env):
        criteria = RecordSearchCriteria(patient_id="NONEXISTENT")
        record = await page_env["repo"].find_random(criteria)
        assert record is None


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


class TestFindPageUniqueViolationFilter:
    """Tests for RecordSearchCriteria.exclude_unique_violations in find_page.

    Verifies that the criteria flag wires the existing
    ``_unique_per_user_violation_filter`` into ``find_page`` for unassigned
    records of ``unique_per_user`` types, while leaving non-unique types and
    superuser-style queries (flag off) untouched.
    """

    @pytest.mark.asyncio
    async def test_flag_hides_unassigned_violating_record(
        self, test_session, test_user, test_patient, test_study
    ):
        rt = RecordType(
            name="upu-study-rt",
            unique_per_user=True,
            level=DicomQueryLevel.STUDY,
        )
        test_session.add(rt)
        await test_session.commit()

        finished = await seed_record(
            test_session,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=None,
            rt_name=rt.name,
            user_id=test_user.id,
            status=RecordStatus.finished,
        )
        unassigned_dup = await seed_record(
            test_session,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=None,
            rt_name=rt.name,
            user_id=None,
            status=RecordStatus.pending,
        )

        repo = RecordRepository(test_session)
        criteria = RecordSearchCriteria(
            user_id=test_user.id,
            include_unassigned=True,
            exclude_unique_violations=True,
        )
        result = await repo.find_page(criteria, cursor=None, limit=50, sort="changed_at_desc")
        ids = [r.id for r in result.records]
        assert finished.id in ids
        assert unassigned_dup.id not in ids

    @pytest.mark.asyncio
    async def test_flag_off_keeps_unassigned_violating_record(
        self, test_session, test_user, test_patient, test_study
    ):
        rt = RecordType(
            name="upu-study-rt-off",
            unique_per_user=True,
            level=DicomQueryLevel.STUDY,
        )
        test_session.add(rt)
        await test_session.commit()

        finished = await seed_record(
            test_session,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=None,
            rt_name=rt.name,
            user_id=test_user.id,
            status=RecordStatus.finished,
        )
        unassigned_dup = await seed_record(
            test_session,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=None,
            rt_name=rt.name,
            user_id=None,
            status=RecordStatus.pending,
        )

        repo = RecordRepository(test_session)
        criteria = RecordSearchCriteria(
            user_id=test_user.id,
            include_unassigned=True,
            exclude_unique_violations=False,
        )
        result = await repo.find_page(criteria, cursor=None, limit=50, sort="changed_at_desc")
        ids = [r.id for r in result.records]
        assert finished.id in ids
        assert unassigned_dup.id in ids

    @pytest.mark.asyncio
    async def test_flag_does_not_hide_non_unique_type(
        self, test_session, test_user, test_patient, test_study
    ):
        rt = RecordType(
            name="non-upu-rt",
            unique_per_user=False,
            level=DicomQueryLevel.STUDY,
        )
        test_session.add(rt)
        await test_session.commit()

        finished = await seed_record(
            test_session,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=None,
            rt_name=rt.name,
            user_id=test_user.id,
            status=RecordStatus.finished,
        )
        unassigned = await seed_record(
            test_session,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=None,
            rt_name=rt.name,
            user_id=None,
            status=RecordStatus.pending,
        )

        repo = RecordRepository(test_session)
        criteria = RecordSearchCriteria(
            user_id=test_user.id,
            include_unassigned=True,
            exclude_unique_violations=True,
        )
        result = await repo.find_page(criteria, cursor=None, limit=50, sort="changed_at_desc")
        ids = [r.id for r in result.records]
        assert finished.id in ids
        assert unassigned.id in ids


@pytest_asyncio.fixture
async def sort_env(test_session: AsyncSession):
    """Seed records with deliberately varied (record_type, status, patient, user, modality)
    so each sort order produces a distinguishable ordering."""
    user_a = make_user(email="a_user@test.com")
    user_b = make_user(email="b_user@test.com")
    test_session.add_all([user_a, user_b])
    await test_session.commit()

    pat_a = make_patient("SORT_PAT_A")
    pat_b = make_patient("SORT_PAT_B")
    pat_c = make_patient("SORT_PAT_C")
    test_session.add_all([pat_a, pat_b, pat_c])
    await test_session.commit()

    study_a = make_study("SORT_PAT_A", "1.2.3.910")
    study_b = make_study("SORT_PAT_B", "1.2.3.911")
    study_c = make_study("SORT_PAT_C", "1.2.3.912")
    test_session.add_all([study_a, study_b, study_c])
    await test_session.commit()

    series_ct = make_series("1.2.3.910", "1.2.3.910.1", num=1)
    series_ct.modality = "CT"
    series_mr = make_series("1.2.3.911", "1.2.3.911.1", num=1)
    series_mr.modality = "MR"
    test_session.add_all([series_ct, series_mr])
    await test_session.commit()

    rt_alpha = make_record_type("sort-rt-alpha")
    rt_beta = make_record_type("sort-rt-beta")
    test_session.add_all([rt_alpha, rt_beta])
    await test_session.commit()

    base_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
    # Five records: each varies one dimension while sharing others, so any
    # asc sort by that dimension yields a predictable order.
    r1 = await seed_record(
        test_session,
        patient_id="SORT_PAT_A",
        study_uid="1.2.3.910",
        series_uid="1.2.3.910.1",
        rt_name="sort-rt-alpha",
        status=RecordStatus.failed,
        user_id=user_a.id,
        changed_at=base_time,
    )
    r2 = await seed_record(
        test_session,
        patient_id="SORT_PAT_A",
        study_uid="1.2.3.910",
        series_uid="1.2.3.910.1",
        rt_name="sort-rt-beta",
        status=RecordStatus.pending,
        user_id=user_b.id,
        changed_at=base_time + timedelta(hours=1),
    )
    r3 = await seed_record(
        test_session,
        patient_id="SORT_PAT_B",
        study_uid="1.2.3.911",
        series_uid="1.2.3.911.1",
        rt_name="sort-rt-alpha",
        status=RecordStatus.finished,
        user_id=user_a.id,
        changed_at=base_time + timedelta(hours=2),
    )
    r4 = await seed_record(
        test_session,
        patient_id="SORT_PAT_C",
        study_uid="1.2.3.912",
        series_uid=None,  # no series → modality NULL
        rt_name="sort-rt-beta",
        status=RecordStatus.inwork,
        user_id=None,  # unassigned → user_id NULL
        changed_at=base_time + timedelta(hours=3),
    )
    r5 = await seed_record(
        test_session,
        patient_id="SORT_PAT_C",
        study_uid="1.2.3.912",
        series_uid=None,
        rt_name="sort-rt-beta",
        status=RecordStatus.blocked,
        user_id=user_b.id,
        changed_at=base_time + timedelta(hours=4),
    )

    repo = RecordRepository(test_session)
    return {
        "repo": repo,
        "records": [r1, r2, r3, r4, r5],
        "users": {"a": user_a, "b": user_b},
    }


class TestFindPageExtendedSort:
    """Server-side sort across non-id, non-changed_at columns."""

    @pytest.mark.asyncio
    async def test_sort_by_record_type_asc(self, sort_env):
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="record_type_asc"
        )
        names = [r.record_type_name for r in result.records]
        assert names == sorted(names)
        # Within a single record_type, tie-break is Record.id ASC.
        alphas = [r.id for r in result.records if r.record_type_name == "sort-rt-alpha"]
        assert alphas == sorted(alphas)

    @pytest.mark.asyncio
    async def test_sort_by_record_type_desc(self, sort_env):
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="record_type_desc"
        )
        names = [r.record_type_name for r in result.records]
        assert names == sorted(names, reverse=True)

    @pytest.mark.asyncio
    async def test_sort_by_status_asc_desc_are_mirrored(self, sort_env):
        """Status sort is DB-dependent (PostgreSQL native enum sorts by
        definition order, SQLite TEXT sorts alphabetically), so the test
        only verifies that ASC and DESC are mirror images of each other and
        that all seeded records are returned."""
        asc = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="status_asc"
        )
        desc = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="status_desc"
        )
        asc_ids = [r.id for r in asc.records]
        desc_ids = [r.id for r in desc.records]
        seeded_ids = {r.id for r in sort_env["records"]}

        # All 5 seeded records present in both directions.
        assert set(asc_ids) == seeded_ids
        assert set(desc_ids) == seeded_ids
        # All seeded records use distinct statuses, so no tie-break
        # ambiguity — desc must be the exact reverse of asc.
        assert asc_ids == desc_ids[::-1]

    @pytest.mark.asyncio
    async def test_sort_by_patient_asc(self, sort_env):
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="patient_asc"
        )
        pats = [r.patient_id for r in result.records]
        assert pats == sorted(pats)

    @pytest.mark.asyncio
    async def test_sort_by_patient_desc(self, sort_env):
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="patient_desc"
        )
        pats = [r.patient_id for r in result.records]
        assert pats == sorted(pats, reverse=True)

    @pytest.mark.asyncio
    async def test_sort_by_user_asc_nulls_last(self, sort_env):
        """user_id sort puts NULL (unassigned) at the end in asc order.

        Does NOT assert the lexicographic order of non-NULL UUIDs against
        `sorted(_, key=str)`: PostgreSQL sorts the `UUID` column by binary
        value, which only coincides with hex-string order for canonical
        UUIDs. The NULLS-LAST partition is the load-bearing property; the
        inter-UUID order is the DB's natural one.
        """
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="user_asc"
        )
        user_ids = [r.user_id for r in result.records]
        non_null = [u for u in user_ids if u is not None]
        nulls = [u for u in user_ids if u is None]
        assert user_ids == non_null + nulls
        assert len(nulls) > 0

    @pytest.mark.asyncio
    async def test_sort_by_user_desc_nulls_last(self, sort_env):
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="user_desc"
        )
        user_ids = [r.user_id for r in result.records]
        non_null = [u for u in user_ids if u is not None]
        nulls = [u for u in user_ids if u is None]
        assert user_ids == non_null + nulls
        assert len(nulls) > 0

    @pytest.mark.asyncio
    async def test_sort_by_modality_asc_nulls_last(self, sort_env):
        """Records without a series have NULL modality — must sort last in asc."""
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="modality_asc"
        )
        modalities = [r.series.modality if r.series else None for r in result.records]
        non_null = [m for m in modalities if m is not None]
        nulls = [m for m in modalities if m is None]
        assert modalities == non_null + nulls
        assert non_null == sorted(non_null)

    @pytest.mark.asyncio
    async def test_sort_by_modality_desc_nulls_last(self, sort_env):
        result = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=10, sort="modality_desc"
        )
        modalities = [r.series.modality if r.series else None for r in result.records]
        non_null = [m for m in modalities if m is not None]
        nulls = [m for m in modalities if m is None]
        assert modalities == non_null + nulls
        assert non_null == sorted(non_null, reverse=True)

    @pytest.mark.asyncio
    async def test_cursor_stability_extended_sort(self, sort_env):
        """Paginating across two pages must yield each record exactly once."""
        first = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=2, sort="patient_asc"
        )
        second = await sort_env["repo"].find_page(
            RecordSearchCriteria(),
            cursor=first.next_cursor,
            limit=10,
            sort="patient_asc",
        )
        all_ids = [r.id for r in first.records] + [r.id for r in second.records]
        assert len(all_ids) == 5
        assert len(set(all_ids)) == 5  # no duplicates

    @pytest.mark.asyncio
    async def test_cursor_stability_descending_sort(self, sort_env):
        """Exercise the `sort_col < literal` branch of `_keyset_where` plus the
        ascending Record.id tie-break used by every non-legacy DESC sort."""
        first = await sort_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=2, sort="patient_desc"
        )
        second = await sort_env["repo"].find_page(
            RecordSearchCriteria(),
            cursor=first.next_cursor,
            limit=10,
            sort="patient_desc",
        )
        all_ids = [r.id for r in first.records] + [r.id for r in second.records]
        assert len(all_ids) == 5
        assert len(set(all_ids)) == 5  # no duplicates
        # First page must hold the largest patient_id values.
        first_patients = [r.patient_id for r in first.records]
        second_patients = [r.patient_id for r in second.records]
        assert max(second_patients) <= min(first_patients)


@pytest_asyncio.fixture
async def null_user_env(test_session: AsyncSession):
    """Seed records with a mix of assigned and unassigned users so that paging
    can stop *inside* the NULL zone — exercising the `cursor_key is None`
    branch of `_keyset_where`."""
    user_a = make_user(email="null_zone_a@test.com")
    test_session.add(user_a)
    await test_session.commit()

    pat = make_patient("NULL_PAT")
    test_session.add(pat)
    await test_session.commit()

    study = make_study("NULL_PAT", "1.2.3.920")
    test_session.add(study)
    await test_session.commit()

    series = make_series("1.2.3.920", "1.2.3.920.1", num=1)
    test_session.add(series)
    await test_session.commit()

    rt = make_record_type("null-user-rt")
    test_session.add(rt)
    await test_session.commit()

    base_time = datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC)
    records = []
    # One record with a user, three without. ASC NULLS LAST → assigned record
    # first, then three NULLs ordered by id.
    records.append(
        await seed_record(
            test_session,
            patient_id="NULL_PAT",
            study_uid="1.2.3.920",
            series_uid="1.2.3.920.1",
            rt_name="null-user-rt",
            user_id=user_a.id,
            changed_at=base_time,
        )
    )
    for i in range(3):
        records.append(
            await seed_record(
                test_session,
                patient_id="NULL_PAT",
                study_uid="1.2.3.920",
                series_uid="1.2.3.920.1",
                rt_name="null-user-rt",
                user_id=None,
                changed_at=base_time + timedelta(hours=i + 1),
            )
        )

    repo = RecordRepository(test_session)
    return {"repo": repo, "records": records}


class TestFindPageNullZoneCursor:
    @pytest.mark.asyncio
    async def test_user_asc_paginates_through_null_zone(self, null_user_env):
        """First page ends on a NULL-user row, so the cursor encodes `k=None`
        and the second page reads from the NULL-only branch of `_keyset_where`.
        """
        first = await null_user_env["repo"].find_page(
            RecordSearchCriteria(), cursor=None, limit=2, sort="user_asc"
        )
        # Page 1: 1 assigned record + 1st NULL record. Cursor key is None
        # because the last record on the page has user_id=None.
        assert len(first.records) == 2
        assert first.records[0].user_id is not None
        assert first.records[1].user_id is None
        assert first.next_cursor is not None

        second = await null_user_env["repo"].find_page(
            RecordSearchCriteria(),
            cursor=first.next_cursor,
            limit=10,
            sort="user_asc",
        )
        # Page 2: the remaining two NULL-user rows, no duplicates.
        assert len(second.records) == 2
        assert all(r.user_id is None for r in second.records)
        first_ids = {r.id for r in first.records}
        second_ids = {r.id for r in second.records}
        assert first_ids.isdisjoint(second_ids)
        all_seeded = {r.id for r in null_user_env["records"]}
        assert first_ids | second_ids == all_seeded
