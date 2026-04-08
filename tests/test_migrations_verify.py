"""Unit tests for ``verify_migrations_applied`` (fail-fast startup check)."""

from unittest.mock import patch

import pytest

from clarinet.exceptions import MigrationError
from clarinet.utils.migrations import verify_migrations_applied


def test_passes_when_no_pending_migrations() -> None:
    """No pending migrations → no raise."""
    with patch("clarinet.utils.migrations.get_pending_migrations", return_value=[]):
        verify_migrations_applied()


def test_raises_when_pending_migrations() -> None:
    """Pending migrations → ``MigrationError`` with count and fix hint."""
    with (
        patch(
            "clarinet.utils.migrations.get_pending_migrations",
            return_value=["abc123", "def456"],
        ),
        pytest.raises(MigrationError, match="2 pending") as exc_info,
    ):
        verify_migrations_applied()

    assert "clarinet db migrate" in str(exc_info.value)
    assert "abc123" in str(exc_info.value)
    assert "def456" in str(exc_info.value)


def test_raises_when_alembic_not_initialized() -> None:
    """``FileNotFoundError`` from helpers → ``MigrationError`` with init hint."""
    with (
        patch(
            "clarinet.utils.migrations.get_pending_migrations",
            side_effect=FileNotFoundError("alembic.ini missing"),
        ),
        pytest.raises(MigrationError, match="Alembic not initialized") as exc_info,
    ):
        verify_migrations_applied()

    assert "clarinet init-migrations" in str(exc_info.value)


def test_raises_when_db_has_no_alembic_version() -> None:
    """Fresh DB (current=None) → re-raised as actionable ``MigrationError``.

    ``get_pending_migrations`` raises a bare ``MigrationError`` when the DB
    has no current revision while alembic has a head. ``verify_migrations_applied``
    must rewrap that into a clear message instead of leaking an empty error.
    """
    with (
        patch(
            "clarinet.utils.migrations.get_pending_migrations",
            side_effect=MigrationError(),
        ),
        pytest.raises(MigrationError, match="fresh database") as exc_info,
    ):
        verify_migrations_applied()

    assert "clarinet db migrate" in str(exc_info.value)
