"""Integration tests for partition-aware ``unique_by`` enforcement.

Covers the bound-tuple rule (``RecordRepository.ensure_unique_by``) across
record creation, parent-inheritance re-check, claim/assign, and the
claimable-pool listing filter (``_unique_by_violation_filter`` /
``_partitioned_unique_type_names``). See ``clarinet/models/uniqueness.py``
for partition canonicalization.
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.record import RecordType
from clarinet.repositories.record_repository import RecordRepository
from tests.utils.factories import make_user, seed_record
from tests.utils.urls import RECORDS_BASE


async def create_record(
    client: AsyncClient,
    record_type_name: str,
    *,
    patient_id: str,
    study_uid: str | None = None,
    series_uid: str | None = None,
    parent_record_id: int | None = None,
    user_id: Any = None,
):
    """POST /api/records/ with the given fields; returns the raw response."""
    payload: dict[str, object] = {
        "patient_id": patient_id,
        "record_type_name": record_type_name,
    }
    if study_uid is not None:
        payload["study_uid"] = study_uid
    if series_uid is not None:
        payload["series_uid"] = series_uid
    if parent_record_id is not None:
        payload["parent_record_id"] = parent_record_id
    if user_id is not None:
        payload["user_id"] = str(user_id)
    return await client.post(f"{RECORDS_BASE}/", json=payload)


async def _make_anchor(test_session, anchor_type, patient, study, series):
    """Persist a bare record (of an unconstrained type) usable as a parent anchor."""
    return await seed_record(
        test_session,
        patient_id=patient.id,
        study_uid=study.study_uid,
        series_uid=series.series_uid,
        rt_name=anchor_type.name,
        status=RecordStatus.pending,
    )


@pytest_asyncio.fixture
async def seed_parented_type(test_session):
    """RecordType with unique_by={"user", "parent"} at SERIES level."""
    rt = RecordType(
        name="ub-parented-ty",
        unique_by=frozenset({"user", "parent"}),
        level=DicomQueryLevel.SERIES,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def seed_user_type(test_session):
    """RecordType with the default unique_by ({"user", "parent"})."""
    rt = RecordType(name="ub-user-ty", level=DicomQueryLevel.SERIES)
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def seed_none_type(test_session):
    """RecordType with unique_by=None and max_records=3.

    Also used as a constraint-free anchor type for parent records in the
    partition-based scenarios below.
    """
    rt = RecordType(name="ub-none-ty", unique_by=None, max_records=3, level=DicomQueryLevel.SERIES)
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def seed_first_check(test_session):
    """RecordType with max_records=2 (STUDY level) + unique_by={"user"}."""
    rt = RecordType(
        name="ub-quota-ty",
        unique_by=frozenset({"user"}),
        max_records=2,
        level=DicomQueryLevel.STUDY,
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def seed_parent_only_type(test_session):
    """RecordType with unique_by={"parent"} only, at SERIES level."""
    rt = RecordType(
        name="ub-parentonly-ty", unique_by=frozenset({"parent"}), level=DicomQueryLevel.SERIES
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest.mark.asyncio
async def test_parent_partition_blocks_same_parent(
    client,
    test_session,
    test_patient,
    test_study,
    test_series,
    seed_parented_type,
    seed_none_type,
    test_user,
):
    """unique_by={"user","parent"}: the same (user, parent) tuple 409s; a
    different parent for the same user is a distinct tuple and succeeds."""
    p1 = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)
    p2 = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)

    r1 = await create_record(
        client,
        seed_parented_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        parent_record_id=p1.id,
        user_id=test_user.id,
    )
    assert r1.status_code == 201

    r2 = await create_record(
        client,
        seed_parented_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        parent_record_id=p1.id,
        user_id=test_user.id,
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "UNIQUE_PER_USER"

    r3 = await create_record(
        client,
        seed_parented_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        parent_record_id=p2.id,
        user_id=test_user.id,
    )
    assert r3.status_code == 201


@pytest.mark.asyncio
async def test_parentless_default_collapses_to_per_user(
    client, test_session, test_patient, test_study, test_series, seed_user_type, test_user
):
    """Default unique_by ({"user","parent"}) collapses to plain per-user
    matching when no record ever sets a parent (NULL == NULL on both sides)."""
    other_user = make_user()
    test_session.add(other_user)
    await test_session.commit()
    await test_session.refresh(other_user)

    r1 = await create_record(
        client,
        seed_user_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        user_id=test_user.id,
    )
    assert r1.status_code == 201

    r2 = await create_record(
        client,
        seed_user_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        user_id=test_user.id,
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "UNIQUE_PER_USER"

    r3 = await create_record(
        client,
        seed_user_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        user_id=other_user.id,
    )
    assert r3.status_code == 201


@pytest.mark.asyncio
async def test_none_only_max_records_binds(
    client, test_patient, test_study, test_series, seed_none_type
):
    """unique_by=None: only max_records binds — 3 succeed, the 4th 409s on quota."""
    for _ in range(3):
        resp = await create_record(
            client,
            seed_none_type.name,
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid=test_series.series_uid,
        )
        assert resp.status_code == 201

    resp = await create_record(
        client,
        seed_none_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "RECORD_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_quota_and_uniqueness_together(
    client, test_session, test_patient, test_study, seed_first_check
):
    """max_records=2 (STUDY) + unique_by={"user"}: 2 distinct users fill the
    quota; a duplicate creation by an existing user 409s on uniqueness before
    the quota is reached; a third distinct user 409s on the quota instead."""
    user_a = make_user()
    user_b = make_user()
    user_c = make_user()
    test_session.add_all([user_a, user_b, user_c])
    await test_session.commit()
    for u in (user_a, user_b, user_c):
        await test_session.refresh(u)

    resp_a = await create_record(
        client,
        seed_first_check.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=user_a.id,
    )
    assert resp_a.status_code == 201

    resp_dup = await create_record(
        client,
        seed_first_check.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=user_a.id,
    )
    assert resp_dup.status_code == 409
    assert resp_dup.json()["code"] == "UNIQUE_PER_USER"

    resp_b = await create_record(
        client,
        seed_first_check.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=user_b.id,
    )
    assert resp_b.status_code == 201

    resp_c = await create_record(
        client,
        seed_first_check.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        user_id=user_c.id,
    )
    assert resp_c.status_code == 409
    assert resp_c.json()["code"] == "RECORD_LIMIT_REACHED"


@pytest.mark.asyncio
async def test_unassigned_pool_coexists(
    client, test_patient, test_study, test_series, seed_user_type
):
    """Bound-tuple rule: unassigned (user_id=None) creations skip the check
    entirely — two unassigned records of the same unique_by={"user",...}
    type in the same context both succeed."""
    r1 = await create_record(
        client,
        seed_user_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
    )
    assert r1.status_code == 201

    r2 = await create_record(
        client,
        seed_user_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
    )
    assert r2.status_code == 201


@pytest.mark.asyncio
async def test_parent_only_dedupes_unassigned(
    client,
    test_session,
    test_patient,
    test_study,
    test_series,
    seed_parent_only_type,
    seed_none_type,
):
    """unique_by={"parent"} has no "user" partition, so the bound-tuple skip
    never triggers: two unassigned records sharing the same parent collide."""
    anchor = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)

    r1 = await create_record(
        client,
        seed_parent_only_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        parent_record_id=anchor.id,
    )
    assert r1.status_code == 201

    r2 = await create_record(
        client,
        seed_parent_only_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        parent_record_id=anchor.id,
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "UNIQUE_PER_USER"


@pytest.mark.asyncio
async def test_unassigned_does_not_block_assigned(
    client,
    test_session,
    test_patient,
    test_study,
    test_series,
    seed_parented_type,
    seed_none_type,
    test_user,
):
    """unique_by={"user","parent"}: an unassigned (None, P) record does not
    block user U from creating their own (U, P) — the existing row's
    user_id=None never matches the ``WHERE user_id = U`` filter."""
    anchor = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)
    await seed_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        rt_name=seed_parented_type.name,
        parent_record_id=anchor.id,
        status=RecordStatus.pending,
    )

    resp = await create_record(
        client,
        seed_parented_type.name,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        parent_record_id=anchor.id,
        user_id=test_user.id,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_claim_enforces_partitions(
    client,
    test_session,
    test_patient,
    test_study,
    test_series,
    seed_parented_type,
    seed_none_type,
    test_user,
):
    """The claim/assign path enforces the same (user, parent) tuple as
    create: U already holds (U, P) — claiming another unassigned (None, P)
    409s; claiming an unassigned (None, Q) with a different parent succeeds."""
    p = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)
    q = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)

    await seed_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        rt_name=seed_parented_type.name,
        user_id=test_user.id,
        parent_record_id=p.id,
        status=RecordStatus.inwork,
    )
    candidate_p = await seed_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        rt_name=seed_parented_type.name,
        parent_record_id=p.id,
        status=RecordStatus.pending,
    )
    candidate_q = await seed_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        rt_name=seed_parented_type.name,
        parent_record_id=q.id,
        status=RecordStatus.pending,
    )

    resp_p = await client.patch(
        f"{RECORDS_BASE}/{candidate_p.id}/user", params={"user_id": str(test_user.id)}
    )
    assert resp_p.status_code == 409
    assert resp_p.json()["code"] == "UNIQUE_PER_USER"

    resp_q = await client.patch(
        f"{RECORDS_BASE}/{candidate_q.id}/user", params={"user_id": str(test_user.id)}
    )
    assert resp_q.status_code == 200


@pytest.mark.asyncio
async def test_claimable_listing_matches_claim(
    test_session,
    test_patient,
    test_study,
    test_series,
    seed_parented_type,
    seed_none_type,
    test_user,
):
    """The claimable-pool filter mirrors claim: U's pool excludes the (None,
    P) record (U already holds (U, P)) but includes (None, Q); a fresh user
    with no prior records sees both."""
    p = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)
    q = await _make_anchor(test_session, seed_none_type, test_patient, test_study, test_series)

    await seed_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        rt_name=seed_parented_type.name,
        user_id=test_user.id,
        parent_record_id=p.id,
        status=RecordStatus.inwork,
    )
    candidate_p = await seed_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        rt_name=seed_parented_type.name,
        parent_record_id=p.id,
        status=RecordStatus.pending,
    )
    candidate_q = await seed_record(
        test_session,
        patient_id=test_patient.id,
        study_uid=test_study.study_uid,
        series_uid=test_series.series_uid,
        rt_name=seed_parented_type.name,
        parent_record_id=q.id,
        status=RecordStatus.pending,
    )

    repo = RecordRepository(test_session)
    pool_for_u = await repo.find_pending_by_user(
        test_user.id, include_unassigned=True, exclude_unique_violations=True
    )
    ids = {r.id for r in pool_for_u}
    assert candidate_p.id not in ids
    assert candidate_q.id in ids

    fresh_user = make_user()
    test_session.add(fresh_user)
    await test_session.commit()
    await test_session.refresh(fresh_user)
    pool_for_fresh = await repo.find_pending_by_user(
        fresh_user.id, include_unassigned=True, exclude_unique_violations=True
    )
    fresh_ids = {r.id for r in pool_for_fresh}
    assert candidate_p.id in fresh_ids
    assert candidate_q.id in fresh_ids
