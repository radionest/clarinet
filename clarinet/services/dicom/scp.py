"""Process-wide Storage SCP singleton for C-MOVE self-retrieval.

When ``dicom_retrieve_mode`` is a c-move mode, Clarinet asks the PACS to send
instances to *us*: the C-MOVE destination AET is our own, and a Storage SCP
must be listening to receive the C-STORE sub-operations. The SCP itself is
``dimsechord.StorageSCP``; this module owns its lifecycle and the question of
which process gets to run one.

Ownership: a listening port belongs to exactly one process, and the PACS routes
C-MOVE by destination AET to a host and port it was configured with. So every
process that retrieves via C-MOVE needs its own registered ``(AET, port)`` —
``clarinet worker --dicom AET:PORT`` gives a worker one, and
``dicom_scp_enabled=false`` marks a process that must not retrieve at all. A
collision fails at startup rather than at the first retrieve, and deliberately
does not pick a free port on its own: a port the operator never registered is
one the PACS cannot route to, so the listener would sit there receiving nothing.

Lifecycle:
    - Started in ``app.py`` lifespan when :func:`storage_scp_wanted` says so,
      and in ``pipeline/worker.py`` only when the worker was asked explicitly
      (``--dicom AET:PORT`` or ``dicom_scp_enabled=true``) — on a c-move
      deployment the API already holds the port, so a worker must not infer
      ownership from the mode
    - Stopped in the matching shutdown block
    - ``get_storage_scp()`` / ``shutdown_storage_scp()`` follow the
      re-create-after-shutdown pattern (see ``clarinet/files/_fs.py``).

Clarinet always registers sessions with ``collect=True``, so the SCP's bounded
streaming queue (and its backpressure) never applies here — instances are
accumulated in the session and drained once the C-MOVE completes.
"""

from dimsechord import StorageSCP

from clarinet.settings import settings

__all__ = [
    "StorageSCP",
    "get_storage_scp",
    "shutdown_storage_scp",
    "start_storage_scp",
    "storage_scp_wanted",
]

_scp: StorageSCP | None = None


def storage_scp_wanted() -> bool:
    """Whether the API process should own a Storage SCP listener.

    The worker does not use this: it takes one only when asked. On a combined
    host the API has already bound the port, and on a worker-only host nothing
    has — but a listener the PACS was never told to route to receives nothing
    either way, so the AET and port have to be given deliberately.

    ``dicom_scp_enabled`` decides when set; otherwise a c-move retrieve mode
    implies one, since that mode cannot retrieve anything without it. The mode
    is the whole test: ``have_dicom`` looks like a guard here but is a worker
    queue-capability flag (it sits with ``have_gpu`` / ``have_quarto`` and
    routes queues), and the API retrieves DICOM without ever setting it.
    """
    if settings.dicom_scp_enabled is not None:
        return settings.dicom_scp_enabled
    return settings.dicom_retrieve_mode in ("c-move", "c-move-study")


def get_storage_scp() -> StorageSCP:
    """Return the module-level StorageSCP singleton, creating it if needed."""
    global _scp
    if _scp is None:
        _scp = StorageSCP()
    return _scp


def start_storage_scp() -> StorageSCP:
    """Start the singleton on the configured local AET/port; no-op if running.

    The API gates this on :func:`storage_scp_wanted`; the worker on an explicit
    ``--dicom`` / ``dicom_scp_enabled=true``.

    Raises:
        OSError: If the port is already taken — by another Clarinet process
            that owns the listener, or by an unrelated service. The message
            carries the ways out, because there is no safe automatic one.
    """
    scp = get_storage_scp()
    if not scp.is_running:
        ip = settings.dicom_ip or "0.0.0.0"
        try:
            scp.start({settings.dicom_aet: settings.dicom_port}, ip)
        except OSError as e:
            raise OSError(
                f"Storage SCP could not bind {ip}:{settings.dicom_port} for AET "
                f"{settings.dicom_aet!r}: {e}. One process per host owns a C-MOVE "
                f"listener on a port. Give this one its own identity, registered on "
                f"the PACS (a worker takes --dicom AET:PORT); or set "
                f"dicom_scp_enabled=false if it should not retrieve via C-MOVE; or "
                f"set dicom_retrieve_mode='c-get' to retrieve over the outbound "
                f"association instead."
            ) from e
    return scp


def shutdown_storage_scp() -> None:
    """Stop the singleton and drop it, so the next lifespan builds a fresh one."""
    global _scp
    if _scp is not None and _scp.is_running:
        _scp.stop()
    _scp = None
