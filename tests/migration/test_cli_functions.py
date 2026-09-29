"""Layer 3: CLI function tests.

Tests the wrapper functions from clarinet.utils.migrations:
cli_init, cli_upgrade, cli_downgrade, cli_current, cli_history, cli_pending,
and underlying functions like get_alembic_config, create_migration, rollback_migration.

Note: CLI functions (cli_*) use Path.cwd() internally, so tests that call them
must chdir to the project directory.
"""

import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic.autogenerate import render_python_code
from alembic.operations import ops
from alembic.runtime.migration import MigrationContext
from alembic.script import Script
from sqlalchemy import Boolean, Column, DateTime, Integer, create_engine, func, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql import expression as sql_expression

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
    render_item,
    rollback_migration,
    run_migrations,
    show_migration_sql,
)

from .conftest import (
    create_pg_database,
    drop_pg_database,
    get_columns,
    init_and_apply,
    override_database_url,
)

pytestmark = pytest.mark.migration


def _upgrade_body(script_path: Path) -> str:
    """Source between ``def upgrade`` and ``def downgrade`` of a generated revision."""
    source = script_path.read_text()
    return source.split("def upgrade() -> None:", 1)[1].split("def downgrade() -> None:", 1)[0]


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

    def test_init_env_py_delegates_to_run_env(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        assert "run_env()" in (project_path / "alembic" / "env.py").read_text()


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

    def test_cli_pending_swallows_bare_migration_error(self, migration_project, monkeypatch):
        """cli_pending reports bare MigrationError as info instead of crashing.

        On a fresh DB or a script/DB mismatch, ``get_pending_migrations``
        raises a bare ``MigrationError``. ``clarinet db migrate status`` is
        a state-reporting command and must not blow up when state is unusual.
        """
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)
        monkeypatch.chdir(project_path)
        rollback_migration(1, project_path)

        # Must not raise
        cli_pending()


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

        script = create_migration("test migration", autogenerate=True, project_path=project_path)

        # Models unchanged since init: autogenerate must find nothing on either
        # dialect (#655 — SQLite reflected the PostgreSQL UUID columns as
        # NUMERIC), and the empty revision must apply.
        assert isinstance(script, Script)
        assert "test_migration" in Path(script.path).name
        assert "op." not in _upgrade_body(Path(script.path))
        run_migrations("head", project_path)


class TestRollbackMultipleSteps:
    """Tests for rollback_migration with multiple steps."""

    def test_rollback_multiple_steps(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        create_migration("second", autogenerate=True, project_path=project_path)
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
                run_migrations("head", tmp_path)
                assert get_current_revision(tmp_path) is not None
        finally:
            drop_pg_database(test_db_name, base_url)


class TestRenderItem:
    """``render_item`` keeps autogenerated server defaults dialect-neutral (#450)."""

    @staticmethod
    def _render(
        column: Column[bool] | Column[datetime] | Column[int],
        prefix: str = "sa.",
        dialect: str = "sqlite",
    ) -> str:
        return render_python_code(
            ops.UpgradeOps(ops=[ops.AddColumnOp("t", column)]),
            sqlalchemy_module_prefix=prefix,
            render_item=render_item,
            migration_context=MigrationContext.configure(dialect_name=dialect),
        )

    @pytest.mark.parametrize(
        ("column", "rendered"),
        [
            (Column("flag", Boolean(), server_default=sql_expression.true()), "sa.true()"),
            (Column("flag", Boolean(), server_default=sql_expression.false()), "sa.false()"),
            (Column("at", DateTime(), server_default=func.now()), "sa.func.now()"),
            # Any other default keeps Alembic's own rendering — not dropped.
            (Column("n", Integer(), server_default=text("0")), "sa.text("),
        ],
    )
    def test_server_default_autogenerated_on_sqlite(
        self, column: Column[bool] | Column[datetime] | Column[int], rendered: str
    ) -> None:
        assert f"server_default={rendered}" in self._render(column)

    def test_now_autogenerated_on_postgresql(self) -> None:
        # Compiled on PostgreSQL this was sa.text('now()'), which SQLite rejects.
        column = Column("at", DateTime(), server_default=func.now())
        assert "server_default=sa.func.now()" in self._render(column, dialect="postgresql")

    def test_uses_configured_module_prefix(self) -> None:
        column = Column("flag", Boolean(), server_default=sql_expression.false())
        assert "server_default=sqlalchemy.false()" in self._render(column, prefix="sqlalchemy.")


# A working env.py as projects had it before run_env() existed.
LEGACY_ENV_PY = """\
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

import clarinet.models  # noqa: F401

config = context.config
connectable = engine_from_config(
    config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
)
with connectable.connect() as connection:
    context.configure(connection=connection, target_metadata=SQLModel.metadata)
    with context.begin_transaction():
        context.run_migrations()
"""


class TestOutdatedEnvPy:
    """An env.py written before run_env() misses every env-level fix — say so."""

    @staticmethod
    def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if r.levelname == "WARNING" and "run_env" in r.getMessage()
        ]

    def test_current_env_py_does_not_warn(
        self, migration_project: tuple[Path, str, Engine], caplog: pytest.LogCaptureFixture
    ) -> None:
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        create_migration("next", autogenerate=True, project_path=project_path)

        assert not self._warnings(caplog)

    def test_env_py_passing_overrides_does_not_warn(
        self, migration_project: tuple[Path, str, Engine], caplog: pytest.LogCaptureFixture
    ) -> None:
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        (project_path / "alembic" / "env.py").write_text(OVERRIDE_ENV_PY)
        create_migration("next", autogenerate=True, project_path=project_path)

        assert not self._warnings(caplog)

    def test_create_migration_warns(
        self, migration_project: tuple[Path, str, Engine], caplog: pytest.LogCaptureFixture
    ) -> None:
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        (project_path / "alembic" / "env.py").write_text(LEGACY_ENV_PY)
        create_migration("next", autogenerate=True, project_path=project_path)

        assert self._warnings(caplog)

    def test_init_migrations_warns_on_existing_env_py(
        self, migration_project: tuple[Path, str, Engine], caplog: pytest.LogCaptureFixture
    ) -> None:
        project_path, _db_url, _engine = migration_project
        (project_path / "alembic").mkdir()
        (project_path / "alembic" / "env.py").write_text(LEGACY_ENV_PY)

        init_alembic_in_project(project_path)

        assert self._warnings(caplog)


# (table, column) pairs that used PostgreSQL's UUID before #655.
LEGACY_UUID_COLUMNS = [
    ("user", "id"),
    ("userroleslink", "user_id"),
    ("record", "user_id"),
    ("record_event", "actor_id"),
    ("access_token", "user_id"),
]


class TestSqliteBatchMigrations:
    """SQLite has no ALTER COLUMN: autogenerate must emit batch ops that keep rows (#655)."""

    UID = "3fa85f6457174562b3fc2c963f66afa6"  # not all-digit: stays TEXT under NUMERIC affinity

    def _legacy_uuid_revision(self, project_path: Path, engine: Engine) -> Script:
        """Autogenerate against a populated pre-#655 SQLite database (columns declared UUID)."""
        if engine.dialect.name != "sqlite":
            pytest.skip("rewrites sqlite_master DDL")
        init_and_apply(project_path)
        uid = self.UID

        with engine.begin() as conn:
            # Rebuild the five tables as a pre-#655 database has them: the
            # column declared UUID. Children keep pointing at "user"; FK
            # enforcement is off here, as on the engine Alembic builds.
            for table, column in LEGACY_UUID_COLUMNS:
                ddl = conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :t"),
                    {"t": table},
                ).scalar_one()
                indexes = (
                    conn.execute(
                        text(
                            "SELECT sql FROM sqlite_master "
                            "WHERE type = 'index' AND tbl_name = :t AND sql IS NOT NULL"
                        ),
                        {"t": table},
                    )
                    .scalars()
                    .all()
                )
                legacy_ddl = ddl.replace(f"{column} CHAR(32)", f"{column} UUID", 1)
                assert legacy_ddl != ddl, f"{table}.{column} is not CHAR(32)"
                conn.execute(text(f'DROP TABLE "{table}"'))
                conn.execute(text(legacy_ddl))
                for index_ddl in indexes:
                    conn.execute(text(index_ddl))
            conn.execute(
                text(
                    'INSERT INTO "user" (id, email, hashed_password, is_active, is_superuser, '
                    "is_verified) VALUES (:id, 'a@example.com', 'x', 1, 0, 0)"
                ),
                {"id": uid},
            )
            conn.execute(text("INSERT INTO userrole (name) VALUES ('doctor')"))
            conn.execute(
                text("INSERT INTO userroleslink (user_id, role_name) VALUES (:id, 'doctor')"),
                {"id": uid},
            )
            # ON DELETE CASCADE / SET NULL children: an FK-enforcing connection
            # would delete or null these when the rebuild DROPs "user".
            conn.execute(
                text(
                    "INSERT INTO access_token (token, user_id, created_at, expires_at, "
                    "last_accessed) VALUES ('tok', :id, '2026-01-01', '2099-01-01', '2026-01-01')"
                ),
                {"id": uid},
            )
            conn.execute(
                text("INSERT INTO record_event (kind, actor_id) VALUES ('created', :id)"),
                {"id": uid},
            )

        script = create_migration("portable uuid", autogenerate=True, project_path=project_path)
        assert isinstance(script, Script)
        return script

    def test_legacy_uuid_column_rebuilt_without_data_loss(
        self, migration_project: tuple[Path, str, Engine]
    ) -> None:
        project_path, _db_url, engine = migration_project
        script = self._legacy_uuid_revision(project_path, engine)

        body = _upgrade_body(Path(script.path))
        for table, _column in LEGACY_UUID_COLUMNS:
            assert f"batch_alter_table('{table}'" in body
        run_migrations("head", project_path)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT id FROM "user"')).scalar_one() == self.UID
            assert conn.execute(text("SELECT user_id FROM userroleslink")).scalar_one() == self.UID
            assert conn.execute(text("SELECT user_id FROM access_token")).scalar_one() == self.UID
            assert conn.execute(text("SELECT actor_id FROM record_event")).scalar_one() == self.UID

        converged = create_migration("again", autogenerate=True, project_path=project_path)

        assert isinstance(converged, Script)
        assert "op." not in _upgrade_body(Path(converged.path))

    def test_legacy_uuid_downgrade_refuses_to_run(
        self, migration_project: tuple[Path, str, Engine]
    ) -> None:
        """The reverse rebuild casts each hex UUID to the reflected NUMERIC: 3fa85f… becomes 3."""
        project_path, _db_url, engine = migration_project
        self._legacy_uuid_revision(project_path, engine)
        run_migrations("head", project_path)

        with pytest.raises(NotImplementedError):
            rollback_migration(1, project_path)

        with engine.connect() as conn:
            assert conn.execute(text('SELECT id FROM "user"')).scalar_one() == self.UID


# A project env.py that keeps a table another tool owns out of autogenerate.
OVERRIDE_ENV_PY = """\
from clarinet.utils.migrations import run_env


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name == "other_tool")


run_env(include_object=include_object)
"""


class TestRunEnv:
    def test_offline_sql_through_run_env(
        self, migration_project: tuple[Path, str, Engine], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Covers the initial revision only: a SQLite table rebuild cannot run offline."""
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        show_migration_sql("head", offline=True, project_path=project_path)

        assert "CREATE TABLE" in capsys.readouterr().out

    def test_autogenerated_ops_are_batched_on_every_dialect(
        self, migration_project: tuple[Path, str, Engine]
    ) -> None:
        """A revision must apply on both dialects, whichever one generated it (#655)."""
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)
        inspector = inspect(engine)
        table, index = next(
            (table, ix["name"])
            for table in inspector.get_table_names()
            for ix in inspector.get_indexes(table)
            if not ix["unique"] and ix["name"]
        )
        with engine.begin() as conn:
            conn.execute(text(f'DROP INDEX "{index}"'))

        script = create_migration("restore index", autogenerate=True, project_path=project_path)

        assert isinstance(script, Script)
        assert "batch_alter_table(" in _upgrade_body(Path(script.path))
        run_migrations("head", project_path)
        assert index in {ix["name"] for ix in inspect(engine).get_indexes(table)}

    def test_now_default_column_added_to_populated_table(
        self, migration_project: tuple[Path, str, Engine]
    ) -> None:
        """SQLite rejects ADD COLUMN with a non-constant default; only a batch rebuild adds it."""
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE pipeline_task_run DROP COLUMN created_at"))
            conn.execute(
                text(
                    "INSERT INTO pipeline_task_run (id, task_name, queue, started_at) "
                    "VALUES ('t1', 'task', 'q', '2026-01-01 00:00:00')"
                )
            )

        create_migration("restore created_at", autogenerate=True, project_path=project_path)
        run_migrations("head", project_path)

        with engine.connect() as conn:
            created_at = conn.execute(
                text("SELECT created_at FROM pipeline_task_run WHERE id = 't1'")
            ).scalar_one()
        assert created_at is not None

    def test_configure_overrides_reach_autogenerate(
        self, migration_project: tuple[Path, str, Engine]
    ) -> None:
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE other_tool (id INTEGER PRIMARY KEY)"))
        (project_path / "alembic" / "env.py").write_text(OVERRIDE_ENV_PY)

        script = create_migration("next", autogenerate=True, project_path=project_path)

        # Without include_object autogenerate emits op.drop_table('other_tool').
        assert isinstance(script, Script)
        assert "op." not in _upgrade_body(Path(script.path))


class TestCrossDialectRegression:
    """Regression for #450: the project scaffold defaults to SQLite, so migrations
    are usually autogenerated there — and then deployed on PostgreSQL."""

    def test_sqlite_generated_migration_is_portable(
        self, migration_project: tuple[Path, str, Engine], tmp_path: Path
    ) -> None:
        project_path, _db_url, engine = migration_project

        with override_database_url(f"sqlite:///{tmp_path / 'autogen.db'}"):
            init_alembic_in_project(project_path)

        # init_alembic_in_project logs and swallows autogenerate errors — check the file.
        (migration,) = (project_path / "alembic" / "versions").glob("*.py")
        source = migration.read_text()
        assert "server_default=sa.false()" in source
        assert "sa.text('0')" not in source
        assert "sa.text('1')" not in source

        if engine.dialect.name == "postgresql":
            run_migrations("head", project_path)
            columns = get_columns(engine, "recordtype")
            assert columns["mask_patient_data"]["default"] == "true"
            assert columns["shared_editing"]["default"] == "false"

    def test_postgresql_generated_revision_applies_on_populated_sqlite(
        self, migration_project: tuple[Path, str, Engine], tmp_path: Path
    ) -> None:
        """The other direction: on PostgreSQL func.now() compiled to sa.text('now()')."""
        project_path, _db_url, engine = migration_project
        if engine.dialect.name != "postgresql":
            pytest.skip("needs revisions autogenerated on PostgreSQL")
        init_and_apply(project_path)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE pipeline_task_run DROP COLUMN created_at"))
        create_migration("restore created_at", autogenerate=True, project_path=project_path)
        initial = get_migration_history(project_path)[-1][0]

        sqlite_url = f"sqlite:///{tmp_path / 'apply.db'}"
        sqlite_engine = create_engine(sqlite_url)
        try:
            with override_database_url(sqlite_url):
                run_migrations(initial, project_path)
                with sqlite_engine.begin() as conn:
                    conn.execute(text("ALTER TABLE pipeline_task_run DROP COLUMN created_at"))
                    conn.execute(
                        text(
                            "INSERT INTO pipeline_task_run (id, task_name, queue, started_at) "
                            "VALUES ('t1', 'task', 'q', '2026-01-01 00:00:00')"
                        )
                    )
                run_migrations("head", project_path)
            with sqlite_engine.connect() as conn:
                created_at = conn.execute(
                    text("SELECT created_at FROM pipeline_task_run WHERE id = 't1'")
                ).scalar_one()
        finally:
            sqlite_engine.dispose()
        assert created_at is not None
