"""Process-wide Storage SCP singleton for C-MOVE self-retrieval.

When ``dicom_retrieve_mode`` is ``c-move``, Clarinet asks the PACS to send
instances to *us*: the C-MOVE destination AET is our own, and a Storage SCP
must be listening to receive the C-STORE sub-operations. The SCP itself is
``dimsechord.StorageSCP``; this module only owns its lifecycle.

Lifecycle:
    - Started in ``app.py`` lifespan when ``dicom_retrieve_mode`` is a c-move
      mode, and in ``pipeline/worker.py`` when the worker runs with ``--dicom``
    - Stopped in the matching shutdown block
    - ``get_storage_scp()`` / ``shutdown_storage_scp()`` follow the
      re-create-after-shutdown pattern (see ``clarinet/files/_fs.py``).

Clarinet always registers sessions with ``collect=True``, so the SCP's bounded
streaming queue (and its backpressure) never applies here — instances are
accumulated in the session and drained once the C-MOVE completes.
"""

from dimsechord import StorageSCP

from clarinet.settings import settings

__all__ = ["StorageSCP", "get_storage_scp", "shutdown_storage_scp", "start_storage_scp"]

_scp: StorageSCP | None = None


def get_storage_scp() -> StorageSCP:
    """Return the module-level StorageSCP singleton, creating it if needed."""
    global _scp
    if _scp is None:
        _scp = StorageSCP()
    return _scp


def start_storage_scp() -> StorageSCP:
    """Start the singleton on the configured local AET/port; no-op if running.

    Raises:
        OSError: If the configured port is already in use.
    """
    scp = get_storage_scp()
    if not scp.is_running:
        scp.start({settings.dicom_aet: settings.dicom_port}, settings.dicom_ip or "0.0.0.0")
    return scp


def shutdown_storage_scp() -> None:
    """Stop the singleton and drop it, so the next lifespan builds a fresh one."""
    global _scp
    if _scp is not None and _scp.is_running:
        _scp.stop()
    _scp = None
