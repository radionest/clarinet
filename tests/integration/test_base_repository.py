"""Integration tests for BaseRepository flush-based create semantics."""

import pytest
import pytest_asyncio
from sqlalchemy.pool import StaticPool

from clarinet.models.patient import Patient
from clarinet.repositories.patient_repository import PatientRepository
from tests.utils.factories import make_patient

# ===================================================================
# flush + refresh semantics in create / create_many
# ===================================================================


class TestCreateFlushSemantics:
    """Verify that create() uses flush (not commit) so the session
    context manager controls the final commit."""

    @pytest_asyncio.fixture
    async def repo(self, test_session):
        """PatientRepository bound to the test session."""
        return PatientRepository(test_session)

    @pytest.mark.asyncio
    async def test_repository_create_returns_refreshed_entity(self, repo):
        """create() returns entity with all attributes after flush + refresh."""
        patient = make_patient("REPO_CREATE_001", "Create Test")
        result = await repo.create(patient)
        assert result.id == "REPO_CREATE_001"
        assert result.name == "Create Test"

    @pytest.mark.asyncio
    async def test_repository_create_does_not_commit(self, test_session, repo):
        """create() flushes but does not commit — data visible in same session but not committed."""
        patient = make_patient("REPO_NOCOMMIT_001", "No Commit Test")
        await repo.create(patient)

        # Data visible in the same session
        fetched = await test_session.get(Patient, "REPO_NOCOMMIT_001")
        assert fetched is not None
        assert fetched.name == "No Commit Test"

        # Rollback should discard data (flush-only, no commit)
        await test_session.rollback()
        fetched_after = await test_session.get(Patient, "REPO_NOCOMMIT_001")
        assert fetched_after is None

    @pytest.mark.asyncio
    async def test_repository_create_many_returns_all_entities(self, repo):
        """create_many() returns all entities refreshed."""
        patients = [make_patient(f"BATCH_{i}", f"Batch {i}") for i in range(5)]
        results = await repo.create_many(patients)
        assert len(results) == 5
        for i, p in enumerate(results):
            assert p.id == f"BATCH_{i}"
            assert p.name == f"Batch {i}"

    @pytest.mark.asyncio
    async def test_create_rollback_on_error(self, test_session, repo):
        """After create() + rollback, entity should not persist (flush-only, no commit)."""
        patient = make_patient("ROLLBACK_001", "Rollback Test")
        await repo.create(patient)

        # Simulate error in route handler
        await test_session.rollback()

        # Entity should not exist
        result = await test_session.get(Patient, "ROLLBACK_001")
        assert result is None


# ===================================================================
# StaticPool selection
# ===================================================================


class TestStaticPoolSelection:
    """Verify StaticPool is used only for in-memory SQLite."""

    def test_static_pool_only_for_memory_sqlite(self):
        """StaticPool should only be used for in-memory SQLite databases."""
        from unittest.mock import patch

        from clarinet.utils.db_manager import DatabaseManager

        # File-based SQLite in debug mode — should NOT use StaticPool
        with patch("clarinet.utils.db_manager.settings") as mock_settings:
            mock_settings.debug = True
            mock_settings.database_driver = "sqlite"
            mock_settings.database_name = "test_db"
            mock_settings.database_url = "sqlite:///test_db.db"
            dm = DatabaseManager()
            engine = dm._create_async_engine()
            assert not isinstance(engine.pool, StaticPool)

        # In-memory SQLite in debug mode — SHOULD use StaticPool
        with patch("clarinet.utils.db_manager.settings") as mock_settings:
            mock_settings.debug = True
            mock_settings.database_driver = "sqlite"
            mock_settings.database_name = ":memory:"
            mock_settings.database_url = "sqlite:///:memory:"
            dm = DatabaseManager()
            engine = dm._create_async_engine()
            assert isinstance(engine.pool, StaticPool)

        # File-based SQLite NOT in debug mode — should NOT use StaticPool
        with patch("clarinet.utils.db_manager.settings") as mock_settings:
            mock_settings.debug = False
            mock_settings.database_driver = "sqlite"
            mock_settings.database_name = "production_db"
            mock_settings.database_url = "sqlite:///production_db.db"
            dm = DatabaseManager()
            engine = dm._create_async_engine()
            assert not isinstance(engine.pool, StaticPool)
