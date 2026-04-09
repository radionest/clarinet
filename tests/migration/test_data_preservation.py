"""Layer 2: Data preservation tests.

Verifies that data survives upgrade/downgrade cycles using bare Alembic
(no clarinet model imports) with hand-written migration scripts.
Uses batch_alter_table for SQLite compatibility.
"""

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from .conftest import get_table_names, init_bare_alembic, write_migration_script

pytestmark = pytest.mark.migration


def _alembic_cfg(project_path, db_url):
    """Get Alembic config pointing at the bare setup."""
    cfg = Config(str(project_path / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_path / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


class TestAddNullableColumn:
    """Scenario 1: Add a nullable column — existing rows get NULL."""

    def test_data_preserved(self, tmp_path, db_backend):
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        # Migration 1: create base table
        write_migration_script(
            versions,
            "aaa1",
            None,
            upgrade_ops='op.create_table("users",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("name", sa.String(100), nullable=False),\n'
            ")",
            downgrade_ops='op.drop_table("users")',
            message="create users",
        )

        command.upgrade(cfg, "head")

        # Seed data
        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (id, name) VALUES (1, 'Alice')"))
            conn.execute(text("INSERT INTO users (id, name) VALUES (2, 'Bob')"))

        # Migration 2: add nullable column
        write_migration_script(
            versions,
            "aaa2",
            "aaa1",
            upgrade_ops='op.add_column("users", sa.Column("phone", sa.String(50), nullable=True))',
            downgrade_ops='with op.batch_alter_table("users") as batch_op:\n'
            '    batch_op.drop_column("phone")',
            message="add phone",
        )

        command.upgrade(cfg, "head")

        # Verify data preserved, phone is NULL
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, phone FROM users ORDER BY id")).fetchall()
        assert len(rows) == 2
        assert rows[0] == (1, "Alice", None)
        assert rows[1] == (2, "Bob", None)

        # Downgrade — data still intact (phone column gone)
        command.downgrade(cfg, "aaa1")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name FROM users ORDER BY id")).fetchall()
        assert len(rows) == 2
        assert rows[0] == (1, "Alice")
        engine.dispose()


class TestAddColumnWithDefault:
    """Scenario 2: Add column with server_default — existing rows get the default."""

    def test_data_preserved(self, tmp_path, db_backend):
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "bbb1",
            None,
            upgrade_ops='op.create_table("users",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("name", sa.String(100), nullable=False),\n'
            ")",
            downgrade_ops='op.drop_table("users")',
            message="create users",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (id, name) VALUES (1, 'Alice')"))

        write_migration_script(
            versions,
            "bbb2",
            "bbb1",
            upgrade_ops='op.add_column("users",\n'
            '    sa.Column("active", sa.Boolean, server_default="1", nullable=False))',
            downgrade_ops='with op.batch_alter_table("users") as batch_op:\n'
            '    batch_op.drop_column("active")',
            message="add active",
        )
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name, active FROM users")).fetchall()
        assert len(rows) == 1
        # server_default="1" → True/1
        assert rows[0][2] in (True, 1)

        command.downgrade(cfg, "bbb1")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name FROM users")).fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, "Alice")
        engine.dispose()


class TestCreateTableWithFK:
    """Scenario 3: Create new table with FK to parent."""

    def test_data_preserved(self, tmp_path, db_backend):
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "ccc1",
            None,
            upgrade_ops='op.create_table("parent",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("name", sa.String(100)),\n'
            ")",
            downgrade_ops='op.drop_table("parent")',
            message="create parent",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO parent (id, name) VALUES (1, 'P1')"))

        write_migration_script(
            versions,
            "ccc2",
            "ccc1",
            upgrade_ops='op.create_table("child",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("parent_id", sa.Integer, sa.ForeignKey("parent.id"), nullable=False),\n'
            '    sa.Column("value", sa.String(100)),\n'
            ")",
            downgrade_ops='op.drop_table("child")',
            message="create child",
        )
        command.upgrade(cfg, "head")

        with engine.begin() as conn:
            conn.execute(text("INSERT INTO child (id, parent_id, value) VALUES (10, 1, 'C1')"))

        # Verify parent data intact
        with engine.connect() as conn:
            parents = conn.execute(text("SELECT * FROM parent")).fetchall()
            children = conn.execute(text("SELECT * FROM child")).fetchall()
        assert len(parents) == 1
        assert len(children) == 1
        assert children[0][1] == 1  # parent_id

        # Downgrade drops child table, parent data preserved
        command.downgrade(cfg, "ccc1")
        tables = get_table_names(engine)
        assert "child" not in tables
        with engine.connect() as conn:
            parents = conn.execute(text("SELECT * FROM parent")).fetchall()
        assert len(parents) == 1
        engine.dispose()


class TestDropTable:
    """Scenario 4: Drop table (with data) and recreate on downgrade."""

    def test_data_preserved(self, tmp_path, db_backend):
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "ddd1",
            None,
            upgrade_ops='op.create_table("old_data",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("value", sa.String(100)),\n'
            ")",
            downgrade_ops='op.drop_table("old_data")',
            message="create old data",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO old_data (id, value) VALUES (1, 'keep')"))

        write_migration_script(
            versions,
            "ddd2",
            "ddd1",
            upgrade_ops='op.drop_table("old_data")',
            downgrade_ops='op.create_table("old_data",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("value", sa.String(100)),\n'
            ")",
            message="drop old data",
        )
        command.upgrade(cfg, "head")

        assert "old_data" not in get_table_names(engine)

        # Downgrade recreates the table (data is lost, but structure is back)
        command.downgrade(cfg, "ddd1")
        assert "old_data" in get_table_names(engine)
        engine.dispose()


class TestAddIndex:
    """Scenario 5: Add index — data unchanged."""

    def test_data_preserved(self, tmp_path, db_backend):
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "eee1",
            None,
            upgrade_ops='op.create_table("items",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("name", sa.String(100)),\n'
            ")",
            downgrade_ops='op.drop_table("items")',
            message="create items",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO items (id, name) VALUES (1, 'A')"))
            conn.execute(text("INSERT INTO items (id, name) VALUES (2, 'B')"))

        write_migration_script(
            versions,
            "eee2",
            "eee1",
            upgrade_ops='op.create_index("ix_items_name", "items", ["name"])',
            downgrade_ops='op.drop_index("ix_items_name", table_name="items")',
            message="add index",
        )
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name FROM items ORDER BY id")).fetchall()
        assert len(rows) == 2
        assert rows[0] == (1, "A")

        command.downgrade(cfg, "eee1")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, name FROM items ORDER BY id")).fetchall()
        assert len(rows) == 2
        engine.dispose()


class TestRenameColumn:
    """Scenario 6: Rename column via batch_alter_table (SQLite-compatible)."""

    def test_data_preserved(self, tmp_path, db_backend):
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "fff1",
            None,
            upgrade_ops='op.create_table("contacts",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("fullname", sa.String(200)),\n'
            ")",
            downgrade_ops='op.drop_table("contacts")',
            message="create contacts",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO contacts (id, fullname) VALUES (1, 'John Doe')"))

        write_migration_script(
            versions,
            "fff2",
            "fff1",
            upgrade_ops='with op.batch_alter_table("contacts") as batch_op:\n'
            '    batch_op.alter_column("fullname", new_column_name="display_name")',
            downgrade_ops='with op.batch_alter_table("contacts") as batch_op:\n'
            '    batch_op.alter_column("display_name", new_column_name="fullname")',
            message="rename column",
        )
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, display_name FROM contacts")).fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, "John Doe")

        command.downgrade(cfg, "fff1")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, fullname FROM contacts")).fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, "John Doe")
        engine.dispose()


class TestAddNotNullBooleanRequiresServerDefault:
    """Regression for PR #144 (``mask_patient_data`` on RecordType).

    Two independent failure modes must be tested, both PostgreSQL-specific:

    1. ``ALTER TABLE ADD COLUMN BOOLEAN NOT NULL`` without any default — PG
       rejects this on populated tables with ``contains null values``.
    2. ``... DEFAULT 1`` (integer literal, what a naive ``text("1")`` produces)
       — PG has no implicit int→bool cast, so even empty tables fail with
       ``default for column is of type integer`` in both CREATE and ALTER.

    SQLite accepts both bad forms silently, which is how each bug slipped
    through the test matrix. The good form uses
    ``sqlalchemy.sql.expression.true()`` / ``false()`` — the only dialect-aware
    Boolean literal (``true`` on PG, ``1`` on SQLite).
    """

    def test_without_server_default_fails_on_postgres(self, tmp_path, db_backend):
        """Mode 1: ALTER ADD COLUMN BOOLEAN NOT NULL on populated PG."""
        if db_backend != "postgresql":
            pytest.skip("This failure mode is PostgreSQL-specific")

        from sqlalchemy.exc import IntegrityError, ProgrammingError

        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "h1",
            None,
            upgrade_ops='op.create_table("rectype",\n'
            '    sa.Column("name", sa.String(30), primary_key=True),\n'
            ")",
            downgrade_ops='op.drop_table("rectype")',
            message="create rectype",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO rectype (name) VALUES ('existing')"))

        write_migration_script(
            versions,
            "h2",
            "h1",
            upgrade_ops=(
                'op.add_column("rectype",\n'
                '    sa.Column("mask_patient_data", sa.Boolean, nullable=False))'
            ),
            downgrade_ops='with op.batch_alter_table("rectype") as batch_op:\n'
            '    batch_op.drop_column("mask_patient_data")',
            message="add bool not null without default",
        )

        with pytest.raises((IntegrityError, ProgrammingError)):
            command.upgrade(cfg, "head")

        engine.dispose()

    def test_text_1_literal_fails_on_postgres(self, tmp_path, db_backend):
        """Mode 2: ``server_default=text("1")`` renders as integer ``1`` on PG.

        This is the trap the original fix for PR #144 fell into (PR #149 v1):
        ``text("1")`` looks portable because SQLite stores BOOLEAN as INTEGER
        and happily accepts ``DEFAULT 1``, but PostgreSQL has no implicit
        int→bool cast and rejects ``DEFAULT 1`` outright — *even on an empty
        table* during CREATE TABLE. The fix is ``sql_expression.true()``.
        """
        if db_backend != "postgresql":
            pytest.skip("This failure mode is PostgreSQL-specific")

        from sqlalchemy.exc import ProgrammingError

        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        # CREATE TABLE with the broken default — should fail on PG even with
        # no data present. Using batch_alter here is irrelevant; this is about
        # the default literal type, not migration strategy.
        write_migration_script(
            versions,
            "j1",
            None,
            upgrade_ops=(
                'op.create_table("rectype",\n'
                '    sa.Column("name", sa.String(30), primary_key=True),\n'
                '    sa.Column("mask_patient_data", sa.Boolean,\n'
                '        server_default=sa.text("1"), nullable=False),\n'
                ")"
            ),
            downgrade_ops='op.drop_table("rectype")',
            message="create with bad int default",
        )

        with pytest.raises(ProgrammingError, match="integer"):
            command.upgrade(cfg, "head")

    def test_with_sql_expression_true_succeeds(self, tmp_path, db_backend):
        """The good pattern: ``server_default=sql_expression.true()``.

        Runs on both backends: PG emits ``DEFAULT true`` (native bool literal),
        SQLite emits ``DEFAULT 1`` (native int literal). Both CREATE TABLE and
        a subsequent ALTER TABLE on a populated table succeed; the existing
        row is backfilled.
        """
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "i1",
            None,
            upgrade_ops='op.create_table("rectype",\n'
            '    sa.Column("name", sa.String(30), primary_key=True),\n'
            ")",
            downgrade_ops='op.drop_table("rectype")',
            message="create rectype",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO rectype (name) VALUES ('existing')"))

        write_migration_script(
            versions,
            "i2",
            "i1",
            upgrade_ops=(
                "from sqlalchemy.sql import expression\n"
                'op.add_column("rectype",\n'
                '    sa.Column("mask_patient_data", sa.Boolean,\n'
                "        server_default=expression.true(), nullable=False))"
            ),
            downgrade_ops='with op.batch_alter_table("rectype") as batch_op:\n'
            '    batch_op.drop_column("mask_patient_data")',
            message="add bool not null with expression.true",
        )
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT name, mask_patient_data FROM rectype WHERE name='existing'")
            ).fetchone()
        assert row is not None
        assert row[0] == "existing"
        # SQLite returns 1, PostgreSQL returns True — accept both.
        assert row[1] in (True, 1)

        engine.dispose()


class TestAddNotNullWithDefault:
    """Scenario 7: Add NOT NULL column with backfill via batch_alter_table."""

    def test_data_preserved(self, tmp_path, db_backend):
        db_url = _setup_db_url(tmp_path, db_backend)
        project = tmp_path / "project"
        project.mkdir()
        versions = init_bare_alembic(project, db_url)
        cfg = _alembic_cfg(project, db_url)

        write_migration_script(
            versions,
            "ggg1",
            None,
            upgrade_ops='op.create_table("accounts",\n'
            '    sa.Column("id", sa.Integer, primary_key=True),\n'
            '    sa.Column("email", sa.String(200)),\n'
            ")",
            downgrade_ops='op.drop_table("accounts")',
            message="create accounts",
        )
        command.upgrade(cfg, "head")

        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO accounts (id, email) VALUES (1, 'a@b.com')"))

        # Add nullable column, backfill, then make NOT NULL via batch
        write_migration_script(
            versions,
            "ggg2",
            "ggg1",
            upgrade_ops=(
                'op.add_column("accounts", sa.Column("status", sa.String(20), nullable=True))\n'
                "op.execute(\"UPDATE accounts SET status = 'active' WHERE status IS NULL\")\n"
                'with op.batch_alter_table("accounts") as batch_op:\n'
                '    batch_op.alter_column("status", nullable=False)'
            ),
            downgrade_ops='with op.batch_alter_table("accounts") as batch_op:\n'
            '    batch_op.drop_column("status")',
            message="add status",
        )
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, email, status FROM accounts")).fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, "a@b.com", "active")

        command.downgrade(cfg, "ggg1")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT id, email FROM accounts")).fetchall()
        assert len(rows) == 1
        assert rows[0] == (1, "a@b.com")
        engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_db_url(tmp_path, db_backend) -> str:
    """Return a DB URL for the given backend."""
    if db_backend == "sqlite":
        return f"sqlite:///{tmp_path}/test.db"
    elif db_backend == "postgresql":
        import atexit

        from .conftest import create_pg_database, drop_pg_database

        worker_db = f"mig_data_{abs(hash(str(tmp_path))) % 100000}"
        db_url, base_url = create_pg_database(worker_db)

        atexit.register(drop_pg_database, worker_db, base_url)

        return db_url
    else:
        pytest.fail(f"Unknown db_backend: {db_backend}")
