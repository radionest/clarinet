"""Shared-record editing: column default, authz bypass, ownership transfer.

``RecordType.shared_editing`` lets any role-holder edit any record of the type
(not only owner/unassigned); each real-user data write reassigns ownership to
the editor. See docs/superpowers/specs/2026-06-26-shared-record-editing-design.md.
"""

from clarinet.models import RecordType


class TestSharedEditingColumn:
    """The additive boolean column defaults off and carries a server_default."""

    def test_defaults_false(self) -> None:
        rt = RecordType(name="shared-col-default", level="SERIES")
        assert rt.shared_editing is False

    def test_has_server_default(self) -> None:
        # Required for a safe ALTER TABLE on populated PostgreSQL.
        col = RecordType.__table__.c.shared_editing
        assert col.server_default is not None
