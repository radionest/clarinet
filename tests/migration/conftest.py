"""Shared fixtures and helpers for migration tests."""

import os
import textwrap
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from clarinet.settings import Settings

# ---------------------------------------------------------------------------
# Settings override
# ---------------------------------------------------------------------------


@contextmanager
def override_database_url(url: str) -> Generator[None]:
    """Patch Settings.database_url property to return the given URL."""
    with patch.object(Settings, "database_url", new_callable=PropertyMock, return_value=url):
        yield


# ---------------------------------------------------------------------------
# Backend parametrization
# ---------------------------------------------------------------------------


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "db_backend" in metafunc.fixturenames:
        backends = ["sqlite"]
        if os.environ.get("CLARINET_TEST_DATABASE_URL"):
            backends.append("postgresql")
        metafunc.parametrize("db_backend", backends)


# ---------------------------------------------------------------------------
# Database-aware project fixture
# ---------------------------------------------------------------------------


def _pg_sync_url(async_url: str) -> str:
    """Convert asyncpg URL to psycopg2 URL for sync Alembic operations."""
    return async_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def create_pg_database(db_name: str) -> tuple[str, str]:
    """Create a PostgreSQL database for testing and return (db_url, base_url).

    Requires CLARINET_TEST_DATABASE_URL env var (asyncpg format).
    Returns a psycopg2-compatible URL.
    """
    async_url = os.environ["CLARINET_TEST_DATABASE_URL"]
    sync_base_url = _pg_sync_url(async_url)
    base_url, _ = sync_base_url.rsplit("/", 1)

    admin_engine = create_engine(f"{base_url}/postgres", isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()

    return f"{base_url}/{db_name}", base_url


def drop_pg_database(db_name: str, base_url: str) -> None:
    """Drop a PostgreSQL database."""
    admin_engine = create_engine(f"{base_url}/postgres", isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    admin_engine.dispose()


@pytest.fixture
def migration_project(
    request: pytest.FixtureRequest, tmp_path: Path, worker_id: str, db_backend: str
) -> Generator[tuple[Path, str, Engine]]:
    """Yield (project_path, db_url, sync_engine) with patched settings.

    SQLite: file-based DB in tmp_path.
    PostgreSQL: creates a unique DB per worker, drops on teardown.
    """
    project_path = tmp_path / "project"
    project_path.mkdir()

    if db_backend == "sqlite":
        db_path = tmp_path / "test.db"
        db_url = f"sqlite:///{db_path}"
        engine = create_engine(db_url)
        with override_database_url(db_url):
            yield project_path, db_url, engine
        engine.dispose()

    elif db_backend == "postgresql":
        async_url = os.environ["CLARINET_TEST_DATABASE_URL"]
        sync_base_url = _pg_sync_url(async_url)
        _, base_db = sync_base_url.rsplit("/", 1)
        worker_db = f"{base_db}_mig_{worker_id}" if worker_id != "master" else f"{base_db}_mig"

        db_url, base_url = create_pg_database(worker_db)
        engine = create_engine(db_url)
        with override_database_url(db_url):
            yield project_path, db_url, engine
        engine.dispose()
        drop_pg_database(worker_db, base_url)

    else:
        pytest.fail(f"Unknown db_backend: {db_backend}")


# ---------------------------------------------------------------------------
# Schema introspection helpers
# ---------------------------------------------------------------------------


def get_table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def get_columns(engine: Engine, table: str) -> dict[str, dict]:
    return {col["name"]: col for col in inspect(engine).get_columns(table)}


def get_foreign_keys(engine: Engine, table: str) -> list[dict]:
    return inspect(engine).get_foreign_keys(table)


def get_indexes(engine: Engine, table: str) -> list[dict]:
    return inspect(engine).get_indexes(table)


def get_unique_constraints(engine: Engine, table: str) -> list[dict]:
    return inspect(engine).get_unique_constraints(table)


def drop_pg_enums(engine: Engine) -> None:
    """Drop all user-defined ENUM types in a PostgreSQL database.

    Alembic downgrade drops tables but leaves orphaned ENUMs, which cause
    'DuplicateObject' on re-upgrade. Call this between downgrade and upgrade.
    No-op on SQLite.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.typname FROM pg_type t "
                "JOIN pg_namespace n ON t.typnamespace = n.oid "
                "WHERE t.typtype = 'e' AND n.nspname = 'public'"
            )
        ).fetchall()
        for (enum_name,) in rows:
            conn.execute(text(f'DROP TYPE IF EXISTS "{enum_name}" CASCADE'))
        conn.commit()


# ---------------------------------------------------------------------------
# Migration init helper
# ---------------------------------------------------------------------------


def init_and_apply(project_path: Path) -> None:
    """Initialize Alembic and apply the initial migration.

    Verifies end-to-end that ``init_alembic_in_project`` produces a migration
    that can be applied without any post-processing — the autogenerated file
    must already contain every import it references (notably ``import sqlmodel``).
    """
    from clarinet.utils.migrations import init_alembic_in_project, run_migrations

    init_alembic_in_project(project_path)
    run_migrations("head", project_path)


# ---------------------------------------------------------------------------
# Bare alembic setup (for Layer 2 — data preservation)
# ---------------------------------------------------------------------------


def init_bare_alembic(project_path: Path, db_url: str) -> Path:
    """Create a minimal alembic setup without clarinet model imports.

    Returns the versions directory path.
    """
    alembic_dir = project_path / "alembic"
    alembic_dir.mkdir(exist_ok=True)
    versions_dir = alembic_dir / "versions"
    versions_dir.mkdir(exist_ok=True)

    # alembic.ini
    ini_content = textwrap.dedent(f"""\
        [alembic]
        script_location = alembic
        sqlalchemy.url = {db_url}

        [loggers]
        keys = root

        [handlers]
        keys = console

        [formatters]
        keys = generic

        [logger_root]
        level = WARN
        handlers = console

        [handler_console]
        class = StreamHandler
        args = (sys.stderr,)
        level = NOTSET
        formatter = generic

        [formatter_generic]
        format = %%(levelname)-5.5s [%%(name)s] %%(message)s
    """)
    (project_path / "alembic.ini").write_text(ini_content)

    # env.py — minimal, no clarinet model imports
    env_content = textwrap.dedent("""\
        from logging.config import fileConfig

        from sqlalchemy import engine_from_config, pool
        from alembic import context

        config = context.config

        if config.config_file_name is not None:
            fileConfig(config.config_file_name)

        target_metadata = None


        def run_migrations_offline():
            url = config.get_main_option("sqlalchemy.url")
            context.configure(
                url=url,
                target_metadata=target_metadata,
                literal_binds=True,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()


        def run_migrations_online():
            connectable = engine_from_config(
                config.get_section(config.config_ini_section, {}),
                prefix="sqlalchemy.",
                poolclass=pool.NullPool,
            )
            with connectable.connect() as connection:
                context.configure(
                    connection=connection,
                    target_metadata=target_metadata,
                    render_as_batch=True,
                )
                with context.begin_transaction():
                    context.run_migrations()


        if context.is_offline_mode():
            run_migrations_offline()
        else:
            run_migrations_online()
    """)
    (alembic_dir / "env.py").write_text(env_content)

    # script.py.mako — not needed for hand-written migrations, but Alembic expects it
    mako_content = textwrap.dedent("""\
        \"\"\"${message}

        Revision ID: ${up_revision}
        Revises: ${down_revision | comma,n}
        Create Date: ${create_date}

        \"\"\"
        from typing import Sequence, Union

        from alembic import op
        import sqlalchemy as sa
        ${imports if imports else ""}

        revision: str = ${repr(up_revision)}
        down_revision: Union[str, None] = ${repr(down_revision)}
        branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
        depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


        def upgrade() -> None:
            ${upgrades if upgrades else "pass"}


        def downgrade() -> None:
            ${downgrades if downgrades else "pass"}
    """)
    (alembic_dir / "script.py.mako").write_text(mako_content)

    return versions_dir


def write_migration_script(
    versions_dir: Path,
    rev_id: str,
    down_rev: str | None,
    upgrade_ops: str,
    downgrade_ops: str,
    message: str = "migration",
) -> Path:
    """Write a migration script file with explicit operations."""
    down_rev_repr = repr(down_rev)
    upgrade_body = textwrap.indent(upgrade_ops, "    ")
    downgrade_body = textwrap.indent(downgrade_ops, "    ")
    content = f'''\
"""{message}

Revision ID: {rev_id}
Revises: {down_rev_repr}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "{rev_id}"
down_revision: Union[str, None] = {down_rev_repr}
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
{upgrade_body}


def downgrade() -> None:
{downgrade_body}
'''
    filename = f"{rev_id}_{message.replace(' ', '_')}.py"
    path = versions_dir / filename
    path.write_text(content)
    return path
