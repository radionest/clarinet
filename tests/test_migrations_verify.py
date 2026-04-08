"""Unit tests for ``verify_migrations_applied`` (fail-fast startup check)."""

from unittest.mock import MagicMock, patch

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
    """Fresh DB (current=None, head exists) → actionable "fresh database" hint.

    ``get_pending_migrations`` raises a bare ``MigrationError`` when the DB
    has no current revision while alembic has a head. ``verify_migrations_applied``
    must rewrap that into a clear, case-specific message.
    """
    mock_script_dir = MagicMock(spec=["get_current_head"])
    mock_script_dir.get_current_head.return_value = "abc123"
    mock_config = MagicMock(spec=[])

    with (
        patch(
            "clarinet.utils.migrations.get_pending_migrations",
            side_effect=MigrationError(),
        ),
        patch(
            "clarinet.utils.migrations.get_alembic_config",
            return_value=mock_config,
        ),
        patch(
            "clarinet.utils.migrations.ScriptDirectory.from_config",
            return_value=mock_script_dir,
        ),
        patch(
            "clarinet.utils.migrations.get_current_revision",
            return_value=None,
        ),
        pytest.raises(MigrationError, match="fresh database") as exc_info,
    ):
        verify_migrations_applied()

    assert "clarinet db migrate" in str(exc_info.value)


def test_raises_on_alembic_state_mismatch() -> None:
    """DB has revision but alembic has no head → state mismatch hint."""
    mock_script_dir = MagicMock(spec=["get_current_head"])
    mock_script_dir.get_current_head.return_value = None  # no scripts
    mock_config = MagicMock(spec=[])

    with (
        patch(
            "clarinet.utils.migrations.get_pending_migrations",
            side_effect=MigrationError(),
        ),
        patch(
            "clarinet.utils.migrations.get_alembic_config",
            return_value=mock_config,
        ),
        patch(
            "clarinet.utils.migrations.ScriptDirectory.from_config",
            return_value=mock_script_dir,
        ),
        patch(
            "clarinet.utils.migrations.get_current_revision",
            return_value="abc123",  # DB ahead of scripts
        ),
        pytest.raises(MigrationError, match="state mismatch") as exc_info,
    ):
        verify_migrations_applied()

    assert "clarinet db migrate status" in str(exc_info.value)
