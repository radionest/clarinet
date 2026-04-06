"""Layer 1: Schema integrity tests.

Verifies that init_alembic_in_project() + run_migrations() produce a schema
matching the current SQLModel metadata — tables, columns, foreign keys,
unique constraints, and indexes.
"""

import pytest
from sqlmodel import SQLModel

from clarinet.models import *  # noqa: F403
from clarinet.utils.migrations import (
    get_current_revision,
    get_pending_migrations,
    run_migrations,
)

from .conftest import (
    get_columns,
    get_foreign_keys,
    get_indexes,
    get_table_names,
    get_unique_constraints,
    init_and_apply,
)

pytestmark = pytest.mark.migration

# Tables defined in SQLModel metadata (excluding alembic_version)
EXPECTED_TABLES = set(SQLModel.metadata.tables.keys())


class TestInitCreatesSchema:
    """Tests that init_alembic_in_project creates the full schema."""

    def test_init_creates_all_tables(self, migration_project):
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)

        tables = get_table_names(engine)
        assert EXPECTED_TABLES.issubset(tables), f"Missing tables: {EXPECTED_TABLES - tables}"
        assert "alembic_version" in tables

    def test_table_columns_match_models(self, migration_project):
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)

        for table_name, table in SQLModel.metadata.tables.items():
            expected_cols = {col.name for col in table.columns}
            actual_cols = set(get_columns(engine, table_name).keys())
            assert expected_cols == actual_cols, (
                f"Column mismatch in {table_name}: expected={expected_cols}, actual={actual_cols}"
            )

    def test_foreign_keys_exist(self, migration_project):
        """Key FK relationships are present in the migrated schema."""
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)

        expected_fks = {
            ("record", "patient", "patient_id"),
            ("record", "study", "study_uid"),
            ("record", "series", "series_uid"),
            ("record", "record", "parent_record_id"),  # self-ref
            ("record_file_link", "record", "record_id"),
            ("record_file_link", "filedefinition", "file_definition_id"),
            ("recordtype_file_link", "recordtype", "record_type_name"),
            ("recordtype_file_link", "filedefinition", "file_definition_id"),
            ("study", "patient", "patient_id"),
            ("series", "study", "study_uid"),
            ("access_token", "user", "user_id"),
            ("userroleslink", "user", "user_id"),
            ("userroleslink", "userrole", "role_name"),
        }

        actual_fks = set()
        for table_name in SQLModel.metadata.tables:
            for fk in get_foreign_keys(engine, table_name):
                referred_table = fk["referred_table"]
                for col in fk["constrained_columns"]:
                    actual_fks.add((table_name, referred_table, col))

        missing = expected_fks - actual_fks
        assert not missing, f"Missing foreign keys: {missing}"

    def test_unique_constraints(self, migration_project):
        """Key unique constraints are enforced."""
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)

        # user.email has a unique index
        user_indexes = get_indexes(engine, "user")
        email_unique = any(idx["unique"] and "email" in idx["column_names"] for idx in user_indexes)
        assert email_unique, "user.email should have a unique index"

        # filedefinition.name — unique constraint
        fd_constraints = get_unique_constraints(engine, "filedefinition")
        fd_indexes = get_indexes(engine, "filedefinition")
        fd_name_unique = any("name" in uc.get("column_names", []) for uc in fd_constraints) or any(
            idx["unique"] and "name" in idx["column_names"] for idx in fd_indexes
        )
        assert fd_name_unique, "filedefinition.name should be unique"

        # patient.anon_name — unique constraint
        pat_constraints = get_unique_constraints(engine, "patient")
        pat_indexes = get_indexes(engine, "patient")
        anon_unique = any(
            "anon_name" in uc.get("column_names", []) for uc in pat_constraints
        ) or any(idx["unique"] and "anon_name" in idx["column_names"] for idx in pat_indexes)
        assert anon_unique, "patient.anon_name should be unique"

    def test_indexes_created(self, migration_project):
        """Key indexes exist in the migrated schema."""
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)

        token_indexes = get_indexes(engine, "access_token")
        index_cols = {col for idx in token_indexes for col in idx["column_names"]}
        assert "expires_at" in index_cols, "access_token.expires_at should be indexed"
        assert "user_id" in index_cols, "access_token.user_id should be indexed"


class TestMigrationOperations:
    """Tests for upgrade/downgrade lifecycle."""

    def test_upgrade_head_idempotent(self, migration_project):
        """Running upgrade head twice doesn't error."""
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)
        # Second upgrade — should be a no-op
        run_migrations("head", project_path)
        assert EXPECTED_TABLES.issubset(get_table_names(engine))

    def test_downgrade_to_base(self, migration_project):
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)

        run_migrations("-1", project_path)

        tables = get_table_names(engine)
        remaining_model_tables = EXPECTED_TABLES & tables
        assert not remaining_model_tables, (
            f"Tables still present after downgrade: {remaining_model_tables}"
        )

    def test_full_roundtrip(self, migration_project):
        """init -> downgrade -> upgrade -> schema matches original."""
        project_path, _db_url, engine = migration_project
        init_and_apply(project_path)

        # Capture initial schema
        initial_tables = get_table_names(engine)
        initial_columns = {}
        for t in EXPECTED_TABLES:
            initial_columns[t] = set(get_columns(engine, t).keys())

        # Downgrade
        run_migrations("-1", project_path)

        # Upgrade back
        run_migrations("head", project_path)

        # Verify schema restored
        restored_tables = get_table_names(engine)
        assert initial_tables == restored_tables

        for t in EXPECTED_TABLES:
            restored_cols = set(get_columns(engine, t).keys())
            assert initial_columns[t] == restored_cols, f"Column mismatch in {t} after roundtrip"

    def test_current_revision_is_head(self, migration_project):
        project_path, _db_url, _engine = migration_project
        init_and_apply(project_path)

        current = get_current_revision(project_path)
        assert current is not None, "Current revision should not be None after init"

        pending = get_pending_migrations(project_path)
        assert pending == [], f"Should have no pending migrations, got: {pending}"
