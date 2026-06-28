"""Process-wide StorageSCP singleton wrapping dimsechord.StorageSCP."""

from dimsechord import StorageSCP

__all__ = ["StorageSCP", "get_storage_scp", "shutdown_storage_scp"]

_storage_scp: StorageSCP | None = None


def get_storage_scp() -> StorageSCP:
    global _storage_scp
    if _storage_scp is None:
        _storage_scp = StorageSCP()
    return _storage_scp


def shutdown_storage_scp() -> None:
    global _storage_scp
    if _storage_scp is not None and _storage_scp.is_running:
        _storage_scp.stop()
    _storage_scp = None  # re-creatable for next lifespan (test compatibility)
