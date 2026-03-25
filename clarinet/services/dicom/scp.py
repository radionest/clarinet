"""Persistent Storage SCP for C-MOVE self-retrieval.

When ``dicom_retrieve_mode`` is ``c-move``, the API process starts a
pynetdicom Storage SCP that listens for incoming C-STORE from the PACS.
Each C-MOVE request registers a ``MoveSession``; the SCP handler deposits
received instances into the matching session and signals completion.

Lifecycle:
    - Started in ``app.py`` lifespan when ``dicom_retrieve_mode == "c-move"``
    - Stopped in the ``finally`` shutdown block
    - Module-level ``get_storage_scp()`` / ``shutdown_storage_scp()`` follow
      the re-create-after-shutdown pattern (see ``clarinet/utils/fs.py``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pynetdicom import AE, StoragePresentationContexts, evt  # type: ignore[import-not-found]
from pynetdicom.sop_class import Verification  # type: ignore[import-not-found,attr-defined]

from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from pydicom import Dataset


@dataclass
class MoveSession:
    """Tracks instances received for a single C-MOVE request.

    Attributes:
        instances: SOPInstanceUID → Dataset mapping of received instances.
        expected_count: Total instances the PACS will send (from C-MOVE pending responses).
        received_count: Number of C-STORE sub-operations received so far.
        done: Signalled when ``received_count >= expected_count``.
    """

    instances: dict[str, Dataset] = field(default_factory=dict)
    expected_count: int | None = None
    received_count: int = 0
    done: threading.Event = field(default_factory=threading.Event)


class StorageSCP:
    """Persistent pynetdicom Storage SCP for receiving C-STORE from PACS.

    Thread safety:
        ``_sessions`` is protected by ``_lock``.  The SCP handler and the
        C-MOVE SCU thread access sessions concurrently — the lock serialises
        all mutations.
    """

    def __init__(self) -> None:
        self._server: Any | None = None
        self._sessions: dict[str, MoveSession] = {}
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """Whether the SCP server is currently accepting connections."""
        return self._server is not None

    # ── Lifecycle ──────────────────────────────────────────────────

    def start(self, aet: str, port: int, ip: str | None = None) -> None:
        """Start the Storage SCP in a background thread.

        Args:
            aet: AE title to listen as.
            port: TCP port to bind.
            ip: IP address to bind (default: all interfaces).

        Raises:
            OSError: If the port is already in use.
        """
        if self._server is not None:
            logger.warning("Storage SCP already running, skipping start")
            return

        ae = AE(ae_title=aet)
        for ctx in StoragePresentationContexts:
            if ctx.abstract_syntax is not None:
                ae.add_supported_context(ctx.abstract_syntax)
        ae.add_supported_context(Verification)

        handlers = [(evt.EVT_C_STORE, self._handle_store)]
        bind_address = (ip or "0.0.0.0", port)
        self._server = ae.start_server(bind_address, evt_handlers=handlers, block=False)  # type: ignore[arg-type]
        logger.info(f"Storage SCP started on {bind_address[0]}:{port} (AET: {aet})")

    def stop(self) -> None:
        """Stop the Storage SCP and clear all pending sessions."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        with self._lock:
            # Signal any waiting threads so they don't hang
            for session in self._sessions.values():
                session.done.set()
            self._sessions.clear()
        logger.info("Storage SCP stopped")

    # ── Session management ────────────────────────────────────────

    def register_session(self, key: str) -> MoveSession:
        """Register a new retrieve session before sending C-MOVE.

        Args:
            key: Correlation key, typically ``"{study_uid}/{series_uid}"``.

        Returns:
            The new ``MoveSession``.

        Raises:
            RuntimeError: If a session with this key is already active.
        """
        session = MoveSession()
        with self._lock:
            if key in self._sessions:
                raise RuntimeError(f"C-MOVE session already active for key={key}")
            self._sessions[key] = session
        logger.debug(f"Registered C-MOVE session: {key}")
        return session

    def set_expected(self, key: str, count: int) -> None:
        """Set the expected instance count (from C-MOVE pending responses).

        If enough instances have already been received, signals ``done``
        immediately.

        Args:
            key: Session key.
            count: Total expected instances.
        """
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                return
            session.expected_count = count
            if count > 0 and session.received_count >= count:
                session.done.set()

    def wait_for_completion(self, key: str, timeout: float) -> MoveSession | None:
        """Block until the session is complete or *timeout* expires.

        Args:
            key: Session key.
            timeout: Seconds to wait.

        Returns:
            The ``MoveSession`` if found, else ``None``.
        """
        with self._lock:
            session = self._sessions.get(key)
        if session is None:
            return None
        session.done.wait(timeout=timeout)
        return session

    def finish_session(self, key: str) -> MoveSession | None:
        """Remove and return the session.

        Args:
            key: Session key.

        Returns:
            The removed ``MoveSession``, or ``None``.
        """
        with self._lock:
            session = self._sessions.pop(key, None)
        if session is not None:
            logger.debug(
                f"Finished C-MOVE session: {key} (received {session.received_count} instances)"
            )
        return session

    # ── SCP event handler ─────────────────────────────────────────

    def _handle_store(self, event: evt.Event) -> int:
        """Handle incoming C-STORE from PACS during C-MOVE.

        Matches the dataset to an active session by StudyInstanceUID
        (and optionally SeriesInstanceUID).

        Returns:
            DICOM status code (0x0000 success, 0xC000 failure).
        """
        try:
            ds = event.dataset
            ds.file_meta = event.file_meta
            study_uid = str(getattr(ds, "StudyInstanceUID", ""))
            series_uid = str(getattr(ds, "SeriesInstanceUID", ""))
            sop_uid = str(getattr(ds, "SOPInstanceUID", ""))

            with self._lock:
                session = self._find_session(study_uid, series_uid)
                if session is None:
                    logger.warning(
                        f"SCP received C-STORE for unregistered session: "
                        f"study={study_uid}, series={series_uid}"
                    )
                    return 0x0000  # Accept anyway — don't reject PACS data

                session.instances[sop_uid] = ds
                session.received_count += 1
                if (
                    session.expected_count is not None
                    and session.received_count >= session.expected_count
                ):
                    session.done.set()

            return 0x0000

        except Exception as e:
            logger.error(f"SCP C-STORE handler error: {e}")
            return 0xC000

    def _find_session(self, study_uid: str, series_uid: str) -> MoveSession | None:
        """Find the matching session for an incoming dataset.

        Tries series-level key first, then study-level.
        Must be called under ``_lock``.
        """
        # Series-level key: "{study_uid}/{series_uid}"
        series_key = f"{study_uid}/{series_uid}"
        session = self._sessions.get(series_key)
        if session is not None:
            return session
        # Study-level key: "{study_uid}/"
        study_key = f"{study_uid}/"
        return self._sessions.get(study_key)


# ── Module-level singleton ────────────────────────────────────────

_scp: StorageSCP | None = None


def get_storage_scp() -> StorageSCP:
    """Return the module-level StorageSCP singleton, creating if needed."""
    global _scp
    if _scp is None:
        _scp = StorageSCP()
    return _scp


def shutdown_storage_scp() -> None:
    """Stop and re-create the singleton (lifespan shutdown pattern)."""
    global _scp
    if _scp is not None:
        _scp.stop()
    _scp = StorageSCP()
