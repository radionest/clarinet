"""Integration tests for parent-child relationships — real SQLite, no mocks.

Covers:
- detect_cycle() utility
- RecordTypeRepository.validate_parent_type() DAG validation
- RecordRepository.validate_parent_record() type matching
- API endpoints: create/update RecordType with parent_type_name, Record with parent_record_id
- Config reconciler: parent_type_name reconciliation + cycle detection
- RecordSearchCriteria filtering by parent_record_id
- user_id inheritance from parent record
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.reconciler import reconcile_record_types
from src.exceptions.domain import (
    RecordNotFoundError,
    RecordTypeNotFoundError,
    ValidationError,
)
from src.repositories.record_repository import RecordRepository, RecordSearchCriteria
from src.repositories.record_type_repository import RecordTypeRepository
from src.utils.graph_validation import detect_cycle
from tests.utils.factories import (
    make_patient,
    make_record_type,
    make_record_type_config,
    make_series,
    make_study,
    make_user,
    seed_record,
)
from tests.utils.urls import RECORD_TYPES, RECORDS_BASE

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
# RecordTypeRepository.validate_parent_type()
# ===================================================================


class TestRecordTypeValidateParentType:
    """Integration tests for DAG validation in RecordTypeRepository."""

    @pytest_asyncio.fixture
    async def env(self, test_session: AsyncSession):
        rt_a = make_record_type("parent_rt_a")
        rt_b = make_record_type("parent_rt_b", parent_type_name="parent_rt_a")
        test_session.add(rt_a)
        await test_session.commit()
        test_session.add(rt_b)
        await test_session.commit()

        repo = RecordTypeRepository(test_session)
        return {"repo": repo, "session": test_session, "rt_a": rt_a, "rt_b": rt_b}

    @pytest.mark.asyncio
    async def test_valid_parent_type(self, env):
        """Setting parent to an existing type with no cycle succeeds."""
        # B already has parent A — creating C with parent B should work
        rt_c = make_record_type("parent_rt_c")
        env["session"].add(rt_c)
        await env["session"].commit()

        # Should not raise
        await env["repo"].validate_parent_type("parent_rt_c", "parent_rt_b")

    @pytest.mark.asyncio
    async def test_none_parent_is_noop(self, env):
        """Setting parent_type_name to None is always valid."""
        await env["repo"].validate_parent_type("parent_rt_a", None)

    @pytest.mark.asyncio
    async def test_parent_not_found(self, env):
        """Non-existent parent type raises RecordTypeNotFoundError."""
        with pytest.raises(RecordTypeNotFoundError):
            await env["repo"].validate_parent_type("parent_rt_a", "nonexistent_rt")

    @pytest.mark.asyncio
    async def test_direct_cycle_rejected(self, env):
        """A -> B; setting A's parent to B creates A -> B -> A cycle."""
        with pytest.raises(ValidationError, match="cycle"):
            await env["repo"].validate_parent_type("parent_rt_a", "parent_rt_b")

    @pytest.mark.asyncio
    async def test_self_reference_rejected(self, env):
        """Setting a type's parent to itself is a cycle."""
        with pytest.raises(ValidationError, match="cycle"):
            await env["repo"].validate_parent_type("parent_rt_a", "parent_rt_a")

    @pytest.mark.asyncio
    async def test_longer_cycle_rejected(self, env):
        """A -> B -> C; setting C's parent to A creates A -> B -> C -> A."""
        rt_c = make_record_type("parent_rt_c", parent_type_name="parent_rt_b")
        env["session"].add(rt_c)
        await env["session"].commit()

        with pytest.raises(ValidationError, match="cycle"):
            await env["repo"].validate_parent_type("parent_rt_a", "parent_rt_c")


# ===================================================================
# RecordRepository.validate_parent_record()
# ===================================================================


class TestRecordValidateParentRecord:
    """Integration tests for parent record validation."""

    @pytest_asyncio.fixture
    async def env(self, test_session: AsyncSession):
        pat = make_patient("VRPAT")
        test_session.add(pat)
        await test_session.commit()

        study = make_study("VRPAT", "1.2.3.600")
        test_session.add(study)
        await test_session.commit()

        series = make_series("1.2.3.600", "1.2.3.600.1")
        test_session.add(series)
        await test_session.commit()

        # Parent type (no parent itself)
        rt_parent = make_record_type("vr_parent_type")
        # Child type that expects parent to be vr_parent_type
        rt_child = make_record_type("vr_child_type", parent_type_name="vr_parent_type")
        # Unrelated type with no parent_type_name
        rt_standalone = make_record_type("vr_standalone")
        test_session.add_all([rt_parent, rt_child, rt_standalone])
        await test_session.commit()

        parent_rec = await seed_record(
            test_session,
            patient_id="VRPAT",
            study_uid="1.2.3.600",
            series_uid="1.2.3.600.1",
            rt_name="vr_parent_type",
        )

        repo = RecordRepository(test_session)
        return {
            "repo": repo,
            "session": test_session,
            "parent_rec": parent_rec,
            "rt_parent": rt_parent,
            "rt_child": rt_child,
        }

    @pytest.mark.asyncio
    async def test_valid_parent_record(self, env):
        """Parent record type matches child's parent_type_name."""
        parent = await env["repo"].validate_parent_record(env["parent_rec"].id, "vr_child_type")
        assert parent.id == env["parent_rec"].id

    @pytest.mark.asyncio
    async def test_parent_record_not_found(self, env):
        """Non-existent parent_record_id raises RecordNotFoundError."""
        with pytest.raises(RecordNotFoundError):
            await env["repo"].validate_parent_record(999999, "vr_child_type")

    @pytest.mark.asyncio
    async def test_child_type_has_no_parent_type_name(self, env):
        """Child type without parent_type_name raises ValidationError."""
        with pytest.raises(ValidationError, match="does not define"):
            await env["repo"].validate_parent_record(env["parent_rec"].id, "vr_standalone")

    @pytest.mark.asyncio
    async def test_parent_type_mismatch(self, env):
        """Parent record's type doesn't match child's parent_type_name."""
        # Create a record of type vr_child_type and try to use it as parent for another child
        child_rec = await seed_record(
            env["session"],
            patient_id="VRPAT",
            study_uid="1.2.3.600",
            series_uid="1.2.3.600.1",
            rt_name="vr_child_type",
        )
        # vr_child_type expects parent of type vr_parent_type, but child_rec IS vr_child_type
        with pytest.raises(ValidationError, match="does not match"):
            await env["repo"].validate_parent_record(child_rec.id, "vr_child_type")


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

        rt_parent = make_record_type("sr_parent_tp")
        rt_child = make_record_type("sr__child_tp", parent_type_name="sr_parent_tp")
        test_session.add_all([rt_parent, rt_child])
        await test_session.commit()

        parent_rec = await seed_record(
            test_session,
            patient_id="SRPAT",
            study_uid="1.2.3.700",
            series_uid="1.2.3.700.1",
            rt_name="sr_parent_tp",
        )

        child_rec = await seed_record(
            test_session,
            patient_id="SRPAT",
            study_uid="1.2.3.700",
            series_uid="1.2.3.700.1",
            rt_name="sr__child_tp",
            parent_record_id=parent_rec.id,
        )

        # Unrelated record (no parent)
        orphan_rec = await seed_record(
            test_session,
            patient_id="SRPAT",
            study_uid="1.2.3.700",
            series_uid="1.2.3.700.1",
            rt_name="sr_parent_tp",
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
# API: RecordType with parent_type_name
# ===================================================================


class TestApiRecordTypeParent:
    """API-level tests for RecordType parent_type_name."""

    @pytest_asyncio.fixture
    async def seed(self, test_session: AsyncSession):
        """Create a base RecordType to use as parent."""
        rt = make_record_type("api_base_type")
        test_session.add(rt)
        await test_session.commit()
        return rt

    @pytest.mark.asyncio
    async def test_create_with_valid_parent(self, client, seed):
        resp = await client.post(
            RECORD_TYPES,
            json={
                "name": "api_child_type",
                "level": "SERIES",
                "parent_type_name": "api_base_type",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["parent_type_name"] == "api_base_type"

    @pytest.mark.asyncio
    async def test_create_with_nonexistent_parent(self, client, seed):
        resp = await client.post(
            RECORD_TYPES,
            json={
                "name": "api_bad_child",
                "level": "SERIES",
                "parent_type_name": "nonexistent_type",
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_with_no_parent(self, client, seed):
        resp = await client.post(
            RECORD_TYPES,
            json={
                "name": "api_no_parent",
                "level": "SERIES",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["parent_type_name"] is None

    @pytest.mark.asyncio
    async def test_update_parent_type_name(self, client, seed):
        # Create a child type without parent first
        resp = await client.post(
            RECORD_TYPES,
            json={
                "name": "api_updatable",
                "level": "SERIES",
            },
        )
        assert resp.status_code == 201

        # Update to set parent
        resp = await client.patch(
            f"{RECORD_TYPES}/api_updatable",
            json={"parent_type_name": "api_base_type"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["parent_type_name"] == "api_base_type"

    @pytest.mark.asyncio
    async def test_update_creates_cycle_rejected(self, client, seed):
        # Create A -> B chain
        resp = await client.post(
            RECORD_TYPES,
            json={
                "name": "api_cycle_child",
                "level": "SERIES",
                "parent_type_name": "api_base_type",
            },
        )
        assert resp.status_code == 201

        # Try to set A's parent to B → creates cycle
        resp = await client.patch(
            f"{RECORD_TYPES}/api_base_type",
            json={"parent_type_name": "api_cycle_child"},
        )
        assert resp.status_code == 422


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

        rt_parent = make_record_type("ar_parent_ty")
        rt_child = make_record_type("ar__child_ty", parent_type_name="ar_parent_ty")
        rt_standalone = make_record_type("ar_standalone")
        test_session.add_all([rt_parent, rt_child, rt_standalone])
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
                "record_type_name": "ar_parent_ty",
            },
        )
        assert resp.status_code == 201
        parent_id = resp.json()["id"]

        # Create child record with parent
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar__child_ty",
                "parent_record_id": parent_id,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["parent_record_id"] == parent_id

    @pytest.mark.asyncio
    async def test_create_record_parent_type_mismatch(self, client, seed):
        # Create a record of standalone type
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar_standalone",
            },
        )
        assert resp.status_code == 201
        standalone_id = resp.json()["id"]

        # Try to use standalone record as parent for child type (expects ar_parent_ty parent)
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar__child_ty",
                "parent_record_id": standalone_id,
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_create_record_nonexistent_parent(self, client, seed):
        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "patient_id": seed["patient_id"],
                "study_uid": seed["study_uid"],
                "series_uid": seed["series_uid"],
                "record_type_name": "ar__child_ty",
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
                "record_type_name": "ar_parent_ty",
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
                "record_type_name": "ar__child_ty",
                "parent_record_id": parent_id,
            },
        )
        assert resp.status_code == 201
        child_data = resp.json()
        assert child_data["user_id"] == str(user.id)

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
                "record_type_name": "ar_parent_ty",
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
                "record_type_name": "ar__child_ty",
                "parent_record_id": parent_id,
                "user_id": str(user_b.id),
            },
        )
        assert resp.status_code == 201
        child_data = resp.json()
        assert child_data["user_id"] == str(user_b.id)


# ===================================================================
# Config Reconciler: parent_type_name
# ===================================================================


class TestReconcilerParentTypeName:
    """Integration tests for parent_type_name in config reconciliation."""

    @pytest.mark.asyncio
    async def test_create_with_parent_type_name(self, test_session: AsyncSession):
        config = [
            make_record_type_config("rec_base_cfg"),
            make_record_type_config("rec_child_cfg", parent_type_name="rec_base_cfg"),
        ]
        result = await reconcile_record_types(config, test_session)

        assert "rec_base_cfg" in result.created
        assert "rec_child_cfg" in result.created
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_update_parent_type_name(self, test_session: AsyncSession):
        # Create initial without parent
        config_v1 = [
            make_record_type_config("rec_upd_base"),
            make_record_type_config("rec_upd_child"),
        ]
        await reconcile_record_types(config_v1, test_session)
        test_session.expire_all()

        # Update child to have parent
        config_v2 = [
            make_record_type_config("rec_upd_base"),
            make_record_type_config("rec_upd_child", parent_type_name="rec_upd_base"),
        ]
        result = await reconcile_record_types(config_v2, test_session)

        assert "rec_upd_child" in result.updated

    @pytest.mark.asyncio
    async def test_reconcile_rejects_cycle(self, test_session: AsyncSession):
        config = [
            make_record_type_config("rec_cyc_a", parent_type_name="rec_cyc_b"),
            make_record_type_config("rec_cyc_b", parent_type_name="rec_cyc_a"),
        ]
        with pytest.raises(ValidationError, match="cycle"):
            await reconcile_record_types(config, test_session)

    @pytest.mark.asyncio
    async def test_reconcile_unchanged_with_parent(self, test_session: AsyncSession):
        config = [
            make_record_type_config("rec_unc_base"),
            make_record_type_config("rec_unc_child", parent_type_name="rec_unc_base"),
        ]
        await reconcile_record_types(config, test_session)
        test_session.expire_all()

        # Same config again → unchanged
        result = await reconcile_record_types(config, test_session)
        assert "rec_unc_base" in result.unchanged
        assert "rec_unc_child" in result.unchanged
