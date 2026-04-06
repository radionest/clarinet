"""Layer 3: CLI function tests.

Tests the wrapper functions from clarinet.utils.migrations:
cli_init, cli_upgrade, cli_downgrade, cli_current, cli_history, cli_pending,
and underlying functions like get_alembic_config, create_migration, rollback_migration.

Note: CLI functions (cli_*) use Path.cwd() internally, so tests that call them
must chdir to the project directory.
"""

import os
from pathlib import Path

import pytest

from clarinet.exceptions import MigrationError
from clarinet.utils.migrations import (
    cli_current,
    cli_downgrade,
    cli_history,
    cli_pending,
    cli_upgrade,
    create_migration,
    get_alembic_config,
    get_current_revision,
    get_migration_history,
    get_pending_migrations,
    init_alembic_in_project,
    rollback_migration,
    run_migrations,
)

from .conftest import (
    create_pg_database,
    drop_pg_database,
    fix_migration_imports,
    init_and_apply,
    override_database_url,
)

pytestmark = pytest.mark.migration


class TestInitFileStructure:
    """Tests for init_alembic_in_project file generation."""

    def test_init_creates_file_structure(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        assert (project_path / "alembic.ini").exists()
        assert (project_path / "alembic" / "env.py").exists()
        assert (project_path / "alembic" / "script.py.mako").exists()
        assert (project_path / "alembic" / "versions").is_dir()

        versions = list((project_path / "alembic" / "versions").glob("*.py"))
        assert len(versions) >= 1, "Should have at least one migration file"

    def test_init_idempotent(self, migration_project):
        """Second init doesn't overwrite existing files."""
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        ini_content = (project_path / "alembic.ini").read_text()
        env_content = (project_path / "alembic" / "env.py").read_text()

        init_alembic_in_project(project_path)

        assert (project_path / "alembic.ini").read_text() == ini_content
        assert (project_path / "alembic" / "env.py").read_text() == env_content

    def test_init_env_py_imports_models(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        env_py = (project_path / "alembic" / "env.py").read_text()
        assert "from clarinet.models import *" in env_py


class TestCliUpgradeDowngrade:
    """Tests for cli_upgrade and cli_downgrade."""

    def test_cli_upgrade_noop_after_init(self, migration_project, monkeypatch):
        """cli_upgrade('head') after init is a no-op (already at head)."""
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)
        monkeypatch.chdir(project_path)
        cli_upgrade("head")

    def test_cli_downgrade_then_upgrade(self, migration_project, monkeypatch):
        from .conftest import drop_pg_enums

        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)
        monkeypatch.chdir(project_path)

        rev_before = get_current_revision(project_path)
        assert rev_before is not None

        cli_downgrade(1)
        rev_after_down = get_current_revision(project_path)
        assert rev_after_down is None

        # PG leaves orphaned ENUM types after downgrade
        drop_pg_enums(engine)

        cli_upgrade("head")
        rev_after_up = get_current_revision(project_path)
        assert rev_after_up == rev_before


class TestCliCurrent:
    """Tests for cli_current."""

    def test_cli_current_runs_without_error(self, migration_project, monkeypatch):
        """cli_current succeeds after init (logs revision via loguru)."""
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)
        monkeypatch.chdir(project_path)

        # cli_current calls get_current_revision twice (known bug line 651),
        # but doesn't crash when alembic is initialized
        cli_current()

        # Verify the underlying function returns a revision
        current = get_current_revision(project_path)
        assert current is not None

    def test_cli_current_missing_alembic(self, tmp_path, monkeypatch):
        """cli_current without init handles error gracefully."""
        monkeypatch.chdir(tmp_path)
        # Should log error and return, not crash
        cli_current()


class TestCliHistory:
    """Tests for cli_history and get_migration_history."""

    def test_cli_history_one_entry(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        history = get_migration_history(project_path)
        assert len(history) >= 1
        messages = [entry[2] for entry in history]
        assert any("initial" in msg.lower() for msg in messages)

    def test_cli_history_missing_alembic(self, tmp_path, monkeypatch):
        """cli_history without init handles error gracefully."""
        monkeypatch.chdir(tmp_path)
        # cli_history catches FileNotFoundError → logs error, returns
        cli_history()  # should not raise


class TestCliPending:
    """Tests for cli_pending and get_pending_migrations."""

    def test_cli_pending_empty(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        pending = get_pending_migrations(project_path)
        assert pending == []

    def test_cli_pending_after_downgrade_raises(self, migration_project):
        """get_pending_migrations raises MigrationError when current is None (at base).

        This is a known limitation: the function can't walk revisions from None to head.
        """
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        rollback_migration(1, project_path)

        with pytest.raises(MigrationError):
            get_pending_migrations(project_path)

    def test_cli_pending_missing_alembic(self, tmp_path, monkeypatch):
        """cli_pending without init handles error gracefully."""
        monkeypatch.chdir(tmp_path)
        # cli_pending catches FileNotFoundError → logs error, returns
        cli_pending()  # should not raise


class TestGetAlembicConfig:
    """Tests for get_alembic_config."""

    def test_get_alembic_config_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Alembic configuration not found"):
            get_alembic_config(tmp_path)


class TestCreateMigration:
    """Tests for create_migration."""

    def test_create_migration_after_init(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        versions_before = set((project_path / "alembic" / "versions").glob("*.py"))

        create_migration("test migration", autogenerate=True, project_path=project_path)

        versions_after = set((project_path / "alembic" / "versions").glob("*.py"))
        new_files = versions_after - versions_before
        assert len(new_files) == 1
        assert "test_migration" in new_files.pop().name


class TestRollbackMultipleSteps:
    """Tests for rollback_migration with multiple steps."""

    def test_rollback_multiple_steps(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        # Create a second empty migration (autogenerate=False to avoid
        # ALTER TABLE statements that SQLite can't handle)
        create_migration("second", autogenerate=False, project_path=project_path)
        run_migrations("head", project_path)

        history = get_migration_history(project_path)
        assert len(history) == 2

        rollback_migration(2, project_path)
        rev = get_current_revision(project_path)
        assert rev is None, "After rolling back all migrations, revision should be None (base)"


class TestAsyncDriverRegression:
    """Regression tests for async-driver URL handling in sync Alembic operations."""

    @pytest.mark.skipif(
        not os.environ.get("CLARINET_TEST_DATABASE_URL"),
        reason="requires real PostgreSQL via CLARINET_TEST_DATABASE_URL",
    )
    def test_init_migrations_with_async_pg_url(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker_id: str
    ) -> None:
        """init-migrations must work when database_url has the asyncpg driver.

        Production code path: user sets database_driver=postgresql+asyncpg
        and runs ``clarinet init-migrations``. Without
        ``Settings.sync_database_url``, Alembic's autogenerate hits
        ``greenlet_spawn has not been called`` because asyncpg is funneled
        through synchronous SQLAlchemy. The fixture-driven migration tests
        miss this because they pre-convert the URL to psycopg2.
        """
        async_template = os.environ["CLARINET_TEST_DATABASE_URL"]
        assert "asyncpg" in async_template, "test requires an async URL"

        # Create an empty PG DB via the sync admin connection. The DB name is
        # scoped per xdist worker so parallel runs don't collide; create_pg_database
        # also DROP IF EXISTS so a crashed prior run leaves no stale state.
        suffix = worker_id if worker_id != "master" else "single"
        test_db_name = f"clarinet_mig_async_regression_{suffix}"
        sync_db_url, base_url = create_pg_database(test_db_name)

        # Build the matching async URL — this is exactly what an end user would
        # have in settings. We deliberately do NOT pre-convert it.
        async_db_url = sync_db_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://")

        try:
            with override_database_url(async_db_url):
                monkeypatch.chdir(tmp_path)

                # Before the fix this swallowed a greenlet error from autogenerate
                # and left versions/ empty. After the fix the migration file exists.
                init_alembic_in_project()

                versions = list((tmp_path / "alembic" / "versions").glob("*.py"))
                assert versions, (
                    "init_alembic_in_project did not autogenerate a migration file "
                    "— Alembic likely failed on the async driver URL"
                )

                # End-to-end: apply the migration through run_migrations, which
                # also reads sync_database_url via get_alembic_config.
                fix_migration_imports(tmp_path)
                run_migrations("head", tmp_path)
                assert get_current_revision(tmp_path) is not None
        finally:
            drop_pg_database(test_db_name, base_url)
