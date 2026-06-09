"""Integration tests for parent-child relationships — real SQLite, no mocks.

Covers:
- detect_cycle() utility
- API endpoints: Record with parent_record_id (existence check lives in
  ``RecordService.create_record``)
- RecordSearchCriteria filtering by parent_record_id
- user_id inheritance from parent record (gated by
  ``RecordType.inherit_user_from_parent``)
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.repositories.record_repository import RecordRepository, RecordSearchCriteria
from clarinet.utils.graph_validation import detect_cycle
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_series,
    make_study,
    make_user,
    seed_record,
)
from tests.utils.urls import RECORDS_BASE

# ===================================================================
# detect_cycle() unit tests
# ===================================================================


class TestDetectCycle:
    """Unit tests for DAG cycle detection utility."""

    def test_no_cycle_linear_chain(self):
        edges = {"A": "B", "B": "C", "C": None}
        assert detect_cycle(edges) is None

    def test_no_cycle_forest(self):
        edges = {"A": "B", "B": None, "C": "D", "D": None}
        assert detect_cycle(edges) is None

    def test_no_cycle_single_node(self):
        edges = {"A": None}
        assert detect_cycle(edges) is None

    def test_no_cycle_empty(self):
        assert detect_cycle({}) is None

    def test_direct_self_cycle(self):
        edges = {"A": "A"}
        cycle = detect_cycle(edges)
        assert cycle is not None
        assert cycle[0] == cycle[-1]  # cycle loops back

    def test_two_node_cycle(self):
        edges = {"A": "B", "B": "A"}
        cycle = detect_cycle(edges)
        assert cycle is not None
        assert len(cycle) == 3  # e.g. ['A', 'B', 'A']

    def test_three_node_cycle(self):
        edges = {"A": "B", "B": "C", "C": "A"}
        cycle = detect_cycle(edges)
        assert cycle is not None
        assert cycle[0] == cycle[-1]

    def test_cycle_with_unrelated_nodes(self):
        edges = {"A": "B", "B": "A", "X": "Y", "Y": None}
        cycle = detect_cycle(edges)
        assert cycle is not None

    def test_all_roots(self):
        edges = {"A": None, "B": None, "C": None}
        assert detect_cycle(edges) is None


# ===================================================================
# RecordSearchCriteria: parent_record_id filter
# ===================================================================


class TestSearchByParentRecordId:
    """Integration tests for filtering records by parent_record_id."""

    @pytest_asyncio.fixture
    async def env(self, test_session: AsyncSession):
        pat = make_patient("SRPAT")
        test_session.add(pat)
        await test_session.commit()

        study = make_study("SRPAT", "1.2.3.700")
        test_session.add(study)
        await test_session.commit()

        series = make_series("1.2.3.700", "1.2.3.700.1")
        test_session.add(series)
        await test_session.commit()

        rt_parent = make_record_type("sr-parent-tp")
        rt_child = make_record_type("sr-child-type")
        test_session.add_all([rt_parent, rt_child])
        await test_session.commit()

        parent_rec = await seed_record(
            test_session,
            patient_id="SRPAT",
            study_uid="1.2.3.700",
            series_uid="1.2.3.700.1",
            rt_name="sr-parent-tp",
        )

        child_rec = await seed_record(
            test_session,
            patient_id="SRPAT",
            study_uid="1.2.3.700",
            series_uid="1.2.3.700.1",
            rt_name="sr-child-type",
            parent_record_id=parent_rec.id,
        )

        # Unrelated record (no parent)
        orphan_rec = await seed_record(
            test_session,
            patient_id="SRPAT",
            study_uid="1.2.3.700",
            series_uid="1.2.3.700.1",
            rt_name="sr-parent-tp",
        )

        repo = RecordRepository(test_session)
        return {
            "repo": repo,
            "parent_rec": parent_rec,
            "child_rec": child_rec,
            "orphan_rec": orphan_rec,
        }

    @pytest.mark.asyncio
    async def test_filter_by_parent_record_id(self, env):
        """Should return only records linked to the given parent."""
        criteria = RecordSearchCriteria(parent_record_id=env["parent_rec"].id)
        records = await env["repo"].find_by_criteria(criteria)

        ids = {r.id for r in records}
        assert env["child_rec"].id in ids
        assert env["parent_rec"].id not in ids
        assert env["orphan_rec"].id not in ids

    @pytest.mark.asyncio
    async def test_no_results_for_nonexistent_parent(self, env):
        """Should return empty when no records have the given parent_record_id."""
        criteria = RecordSearchCriteria(parent_record_id=999999)
        records = await env["repo"].find_by_criteria(criteria)
        assert len(records) == 0


# ===================================================================
# API: Record with parent_record_id
# ===================================================================


class TestApiRecordParent:
    """API-level tests for Record parent_record_id."""

    @pytest_asyncio.fixture
    async def seed(self, test_session: AsyncSession):
        pat = make_patient("ARPAT")
        test_session.add(pat)
        await test_session.commit()

        study = make_study("ARPAT", "1.2.3.800")
        test_session.add(study)
        await test_session.commit()

        series = make_series("1.2.3.800", "1.2.3.800.1")
        test_session.add(series)
        await test_session.commit()

        rt_parent = make_record_type("ar-parent-ty")
        # Opt-in: user_id inheritance from parent is gated by this flag
        rt_child = make_record_type("ar-child-type", inherit_user_from_parent=True)
        rt_noinherit = make_record_type("ar-noinherit-t")
        test_session.add_all([rt_parent, rt_child, rt_noinherit])
        await test_session.commit()

        return {
            "patient_id": "ARPAT",
            "study_uid": "1.2.3.800",
            "series_uid": "1.2.3.800.1",
        }

    @pytest.mark.asyncio
    async def test_create_record_with_parent(self, client, seed):
        # Create parent record
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-parent-ty",
            },
        )
        assert resp.status_code == 201
        parent_id = resp.json()["id"]

        # Create child record with parent — any type can link to any parent
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-child-type",
                "parent_record_id": parent_id,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["parent_record_id"] == parent_id

    @pytest.mark.asyncio
    async def test_create_record_nonexistent_parent(self, client, seed):
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-child-type",
                "parent_record_id": 999999,
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_user_id_inherited_from_parent(self, client, test_session, seed):
        # Create user
        user = make_user()
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        # Create parent with user_id
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-parent-ty",
                "user_id": str(user.id),
            },
        )
        assert resp.status_code == 201
        parent_id = resp.json()["id"]

        # Create child without explicit user_id — should inherit from parent
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-child-type",
                "parent_record_id": parent_id,
            },
        )
        assert resp.status_code == 201
        child_data = resp.json()
        assert child_data["user_id"] == str(user.id)

    @pytest.mark.asyncio
    async def test_user_id_not_inherited_by_default(self, client, test_session, seed):
        """Without inherit_user_from_parent on the type, user_id stays None."""
        user = make_user()
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-parent-ty",
                "user_id": str(user.id),
            },
        )
        assert resp.status_code == 201
        parent_id = resp.json()["id"]

        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-noinherit-t",
                "parent_record_id": parent_id,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["user_id"] is None

    @pytest.mark.asyncio
    async def test_inherited_user_id_respects_unique_per_user(self, client, test_session, seed):
        """Inheritance re-checks unique_per_user with the inherited user.

        The route-level constraint check runs with the payload user_id
        (None here) and cannot see the inherited one — the service must
        re-check and reject with 409.
        """
        user = make_user()
        test_session.add(user)
        await test_session.commit()
        await test_session.refresh(user)

        # The user already owns a record of the child type in this series
        # context (ar-child-type has unique_per_user=True by default).
        await seed_record(
            test_session,
            patient_id=seed["patient_id"],
            study_uid=seed["study_uid"],
            series_uid=seed["series_uid"],
            rt_name="ar-child-type",
            user_id=user.id,
        )

        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-parent-ty",
                "user_id": str(user.id),
            },
        )
        assert resp.status_code == 201
        parent_id = resp.json()["id"]

        # Inherited user would duplicate their existing child record → 409
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-child-type",
                "parent_record_id": parent_id,
            },
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_explicit_user_id_not_overridden(self, client, test_session, seed):
        # Create two users
        user_a = make_user()
        user_b = make_user()
        test_session.add_all([user_a, user_b])
        await test_session.commit()
        await test_session.refresh(user_a)
        await test_session.refresh(user_b)

        # Create parent with user_a
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-parent-ty",
                "user_id": str(user_a.id),
            },
        )
        assert resp.status_code == 201
        parent_id = resp.json()["id"]

        # Create child with explicit user_b — should NOT be overridden
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar-child-type",
                "parent_record_id": parent_id,
                "user_id": str(user_b.id),
            },
        )
        assert resp.status_code == 201
        child_data = resp.json()
        assert child_data["user_id"] == str(user_b.id)
