"""End-to-end integration tests for `POST /records/find` sort + cursor
pagination across more than one page of results.

The fixture seeds >100 records so `limit=100` always splits into at least
two pages, and each sort order is checked for:
  - correct in-page ordering for the column being sorted on
  - cross-page stability (cursor must not yield duplicates or skip rows)
  - no overlap between consecutive pages (strict total ordering)

Unlike `test_record_page.py` which goes through `RecordRepository.find_page`
directly, this file drives the API endpoint through the FastAPI test
client. That covers the routing layer, request validation, response
serialization, and the criteria-builder pipeline together.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.repositories.patient_repository import PatientRepository
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    make_user,
    seed_record,
)
from tests.utils.urls import RECORDS_FIND

PAGE_LIMIT = 100
TOTAL_RECORDS = 220  # > 2x PAGE_LIMIT — third page exercises another cursor


@pytest_asyncio.fixture
async def big_dataset(test_session: AsyncSession):
    """Seed `TOTAL_RECORDS` records spread across multiple patients/studies
    /series/statuses/users so every sort order has a non-trivial ordering
    across two pages."""
    # Two extra users so user-sort has multiple non-NULL values to order.
    user_a = make_user(email="bigds_a@test.com")
    user_b = make_user(email="bigds_b@test.com")
    test_session.add_all([user_a, user_b])
    await test_session.commit()

    # 30 patients with one study each, half of the studies also get a series.
    repo_pat = PatientRepository(test_session)
    patients = []
    studies = []
    series_list = []
    for i in range(30):
        p = await repo_pat.create(make_patient(f"BIGDS_PAT_{i:03d}"))
        patients.append(p)
        study = make_study(p.id, f"1.2.3.940.{i:03d}")
        test_session.add(study)
        studies.append(study)
    await test_session.commit()

    for i, study in enumerate(studies):
        if i % 2 == 0:
            modality = "MR" if i % 4 == 0 else "CT"
            ser = make_series(study.study_uid, f"{study.study_uid}.1", num=1)
            ser.modality = modality
            test_session.add(ser)
            series_list.append(ser)
    await test_session.commit()

    # Two record types: one STUDY level (no series needed) + one SERIES
    # level (so half the rows have a series, half don't — for modality sort).
    rt_study = make_record_type("bigds-study-rt")
    rt_study.level = DicomQueryLevel.STUDY
    rt_series = make_record_type("bigds-series-rt")
    rt_series.level = DicomQueryLevel.SERIES
    test_session.add_all([rt_study, rt_series])
    await test_session.commit()

    series_by_study = {s.study_uid: s for s in series_list}
    statuses = list(RecordStatus)
    users = [user_a, user_b, None]  # None → unassigned, exercises NULLS LAST

    base_time = datetime(2025, 9, 1, 12, 0, 0, tzinfo=UTC)
    records = []
    for i in range(TOTAL_RECORDS):
        study = studies[i % len(studies)]
        if i % 2 == 0 and study.study_uid in series_by_study:
            rt_name = rt_series.name
            ser = series_by_study[study.study_uid]
            series_uid: str | None = ser.series_uid
        else:
            rt_name = rt_study.name
            series_uid = None
        rec = await seed_record(
            test_session,
            patient_id=study.patient_id,
            study_uid=study.study_uid,
            series_uid=series_uid,
            rt_name=rt_name,
            status=statuses[i % len(statuses)],
            user_id=users[i % len(users)].id if users[i % len(users)] else None,
            changed_at=base_time + timedelta(minutes=i),
        )
        records.append(rec)

    return {"records": records}


async def _fetch_all_pages(client: AsyncClient, body: dict[str, Any]) -> list[dict[str, Any]]:
    """Drain every page from /records/find with the given filter+sort body."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):  # safety bound
        payload = {**body, "limit": PAGE_LIMIT}
        if cursor is not None:
            payload["cursor"] = cursor
        resp = await client.post(RECORDS_FIND, json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        items.extend(data["items"])
        cursor = data.get("next_cursor")
        if cursor is None:
            break
    return items


class TestSortPaginationCrossPagesViaApi:
    """Verify that every sort order produces a stable total ordering when
    the result set spans multiple pages."""

    @pytest.mark.asyncio
    async def test_id_asc_pages_are_strictly_increasing(self, client: AsyncClient, big_dataset):
        items = await _fetch_all_pages(client, {"sort": "id_asc"})
        ids = [r["id"] for r in items]
        assert len(ids) == TOTAL_RECORDS
        assert ids == sorted(ids)
        assert len(set(ids)) == TOTAL_RECORDS

    @pytest.mark.asyncio
    async def test_id_desc_pages_are_strictly_decreasing(self, client: AsyncClient, big_dataset):
        items = await _fetch_all_pages(client, {"sort": "id_desc"})
        ids = [r["id"] for r in items]
        assert len(ids) == TOTAL_RECORDS
        assert ids == sorted(ids, reverse=True)
        assert len(set(ids)) == TOTAL_RECORDS

    @pytest.mark.asyncio
    async def test_id_asc_first_page_is_disjoint_from_second(
        self, client: AsyncClient, big_dataset
    ):
        """Explicit two-page check — the cursor must not let any row appear twice."""
        page1 = await client.post(RECORDS_FIND, json={"sort": "id_asc", "limit": PAGE_LIMIT})
        ids1 = [r["id"] for r in page1.json()["items"]]
        cursor = page1.json()["next_cursor"]
        assert cursor is not None
        page2 = await client.post(
            RECORDS_FIND,
            json={"sort": "id_asc", "limit": PAGE_LIMIT, "cursor": cursor},
        )
        ids2 = [r["id"] for r in page2.json()["items"]]
        assert max(ids1) < min(ids2)
        assert set(ids1).isdisjoint(set(ids2))

    @pytest.mark.asyncio
    async def test_changed_at_desc_pages_are_strictly_decreasing(
        self, client: AsyncClient, big_dataset
    ):
        items = await _fetch_all_pages(client, {"sort": "changed_at_desc"})
        assert len(items) == TOTAL_RECORDS
        timestamps = [r["changed_at"] for r in items]
        assert timestamps == sorted(timestamps, reverse=True)

    @pytest.mark.asyncio
    async def test_patient_asc_pages_are_non_decreasing(self, client: AsyncClient, big_dataset):
        items = await _fetch_all_pages(client, {"sort": "patient_asc"})
        patient_ids = [r["patient_id"] for r in items]
        assert patient_ids == sorted(patient_ids)
        assert len(items) == TOTAL_RECORDS

    @pytest.mark.asyncio
    async def test_patient_desc_pages_are_non_increasing(self, client: AsyncClient, big_dataset):
        items = await _fetch_all_pages(client, {"sort": "patient_desc"})
        patient_ids = [r["patient_id"] for r in items]
        assert patient_ids == sorted(patient_ids, reverse=True)

    @pytest.mark.asyncio
    async def test_record_type_asc_pages_are_non_decreasing(self, client: AsyncClient, big_dataset):
        items = await _fetch_all_pages(client, {"sort": "record_type_asc"})
        names = [r["record_type_name"] for r in items]
        assert names == sorted(names)

    @pytest.mark.asyncio
    async def test_user_asc_nulls_last_across_pages(self, client: AsyncClient, big_dataset):
        """Once the non-NULL user_id zone is exhausted, every remaining page
        must hold only NULL-user records."""
        items = await _fetch_all_pages(client, {"sort": "user_asc"})
        user_ids = [r["user_id"] for r in items]
        non_null = [u for u in user_ids if u is not None]
        nulls = [u for u in user_ids if u is None]
        assert user_ids == non_null + nulls
        assert non_null == sorted(non_null, key=str)
        # We seeded one out of every three records as unassigned.
        assert len(nulls) > 0

    @pytest.mark.asyncio
    async def test_modality_asc_nulls_last_across_pages(self, client: AsyncClient, big_dataset):
        items = await _fetch_all_pages(client, {"sort": "modality_asc"})
        modalities = [r["series"]["modality"] if r.get("series") else None for r in items]
        non_null = [m for m in modalities if m is not None]
        nulls = [m for m in modalities if m is None]
        assert modalities == non_null + nulls
        assert non_null == sorted(non_null)

    @pytest.mark.asyncio
    async def test_filter_plus_sort_cross_pages(self, client: AsyncClient, big_dataset):
        """Filter on a single record_type → ~half of TOTAL_RECORDS rows,
        which still exceeds PAGE_LIMIT, so cursor pagination must engage."""
        items = await _fetch_all_pages(
            client,
            {"sort": "id_asc", "record_type_name": "bigds-study-rt"},
        )
        ids = [r["id"] for r in items]
        # Every row must match the filter and be id-sorted.
        assert all(r["record_type_name"] == "bigds-study-rt" for r in items)
        assert ids == sorted(ids)
        assert len(ids) > PAGE_LIMIT  # would not stress cursor otherwise

    @pytest.mark.asyncio
    async def test_wo_user_filter_returns_only_unassigned(self, client: AsyncClient, big_dataset):
        """`wo_user=true` is the body-key that the frontend sends for the
        `__unassigned__` UI value — it must keep working alongside cursor
        pagination."""
        items = await _fetch_all_pages(client, {"sort": "id_asc", "wo_user": True})
        assert all(r["user_id"] is None for r in items)
        assert len(items) > 0
