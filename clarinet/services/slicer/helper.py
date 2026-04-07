"""
Slicer-side DSL helper for concise workspace setup.

This file is read as text by SlicerService and prepended to every script sent
to 3D Slicer. It also remains importable for testing (with DummySlicer fallback).

Usage inside Slicer (what the user writes)::

    s = SlicerHelper(working_folder)
    vol = s.load_volume(input_volume, window=(-1100, 35))

    seg = s.create_segmentation(segment_name)
    seg.add_segment("LungNodes", color=(0, 0, 1))

    s.setup_editor(seg, effect="Paint", brush_size=30, threshold=(-400, 500))
    s.set_layout("axial")
    s.annotate(patient_name)
    s.configure_slab(thickness=10)
    s.add_view_shortcuts()
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    slicer: Any
    qt: Any
    vtk: Any
    ctk: Any
else:
    try:
        import ctk  # type: ignore[import-not-found]
        import qt  # type: ignore[import-not-found]
        import slicer  # type: ignore[import-not-found]
        import vtk  # type: ignore[import-not-found]
    except ImportError:

        class _Dummy:
            """Dummy module for running outside 3D Slicer environment."""

            mrmlScene: Any = None
            app: Any = None
            util: Any = None
            vtkMRMLLayoutNode: Any = None
            modules: Any = None

            def __getattr__(self, name: str) -> Any:
                return None

        slicer = _Dummy()
        qt = _Dummy()
        vtk = _Dummy()
        ctk = _Dummy()

EditorEffectName = Literal["Paint", "Erase", "Threshold", "Draw", "Islands"]


class SlicerHelperError(Exception):
    """Error raised by helper functions when Slicer operations fail."""


def export_segmentation(name: str, output_path: str) -> str:
    """Find segmentation node by name, export to file, and verify.

    Args:
        name: Display name of the segmentation node in the scene.
        output_path: Absolute path where the segmentation file will be saved.

    Returns:
        The output_path on success.

    Raises:
        SlicerHelperError: If the node is not found or the file was not created.
    """
    seg_node = slicer.util.getNode(name)
    if seg_node is None:
        raise SlicerHelperError(f"Segmentation node '{name}' not found in scene")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    slicer.util.exportNode(seg_node, output_path)

    if not os.path.isfile(output_path):
        raise SlicerHelperError(f"Export failed: file not created at {output_path}")

    return output_path


def _find_segment_id(vtk_seg: Any, name: str) -> str | None:
    """Find segment ID by display name.

    Args:
        vtk_seg: A vtkSegmentation object.
        name: Segment display name to search for.

    Returns:
        Segment ID string, or None if not found.
    """
    for i in range(vtk_seg.GetNumberOfSegments()):
        sid = vtk_seg.GetNthSegmentID(i)
        if vtk_seg.GetSegment(sid).GetName() == name:
            return str(sid)
    return None


def clear_scene() -> None:
    """Clear the current Slicer MRML scene."""
    slicer.mrmlScene.Clear(0)


LAYOUT_MAP: dict[str, str] = {
    "axial": "SlicerLayoutOneUpRedSliceView",
    "sagittal": "SlicerLayoutOneUpYellowSliceView",
    "coronal": "SlicerLayoutOneUpGreenSliceView",
    "four_up": "SlicerLayoutFourUpView",
}


class SegmentationBuilder:
    """Builder for segmentation with fluent API."""

    def __init__(self, node: Any, image_node: Any) -> None:
        self.node = node
        self._image_node = image_node
        self._segmentation = node.GetSegmentation()

    def add_segment(
        self, name: str, color: tuple[float, float, float] = (1.0, 0.0, 0.0)
    ) -> SegmentationBuilder:
        """Add a segment with name and RGB color. Returns self for chaining."""
        self._segmentation.AddEmptySegment(name, name, color)
        return self

    def select_segment(self, name: str) -> None:
        """Set active segment in editor by name."""
        slicer.util.selectModule("SegmentEditor")
        editor = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        editor.setCurrentSegmentID(name)


_pacs_log = logging.getLogger("clarinet.slicer.pacs")


def _get_pacs_helper(server_name: str | None = None) -> PacsHelper:
    """Create PacsHelper from context variables (injected by Clarinet) or Slicer config.

    Priority:
    1. Context variables (pacs_host, pacs_port, pacs_aet) — set by build_slicer_context()
    2. Slicer QSettings — fallback for standalone/manual usage

    Uses globals() because this module is executed as injected script text inside
    Slicer — context variables are set as module-level globals by SlicerService.
    """
    g = globals()
    if "pacs_host" in g and "pacs_port" in g and "pacs_aet" in g:
        host = g["pacs_host"]
        port = int(g["pacs_port"])
        called_aet = g["pacs_aet"]
        retrieve_mode = g.get("dicom_retrieve_mode", "c-get")
        # calling_aet and move_aet must come from Slicer's own config —
        # each user's Slicer has its own AE title for C-MOVE destination
        slicer_pacs = PacsHelper.from_slicer(server_name)
        _pacs_log.info(
            f"Using PACS from Clarinet settings: {host}:{port} "
            f"(AET={called_aet}, mode={retrieve_mode}, "
            f"move_dest={slicer_pacs.calling_aet})"
        )
        return PacsHelper(
            host=host,
            port=port,
            called_aet=called_aet,
            calling_aet=slicer_pacs.calling_aet,
            retrieve_mode=retrieve_mode,
            move_aet=slicer_pacs.move_aet,
        )
    return PacsHelper.from_slicer(server_name)


class PacsHelper:
    """DSL for PACS query/retrieve inside 3D Slicer via DIMSE.

    Wraps ``ctkDICOMQuery`` + ``ctkDICOMRetrieve`` into concise methods.
    Use ``PacsHelper.from_slicer()`` to read connection params from Slicer's
    DICOM module, or pass them explicitly for testing.
    """

    # Valid retrieve strategies:
    #   c-get        — C-GET per series, fallback to C-MOVE per series
    #   c-get-study  — C-GET whole study, fallback to C-MOVE whole study
    #   c-move       — C-MOVE per series (no C-GET attempt)
    #   c-move-study — C-MOVE whole study (no C-GET attempt)
    VALID_MODES = ("c-get", "c-get-study", "c-move", "c-move-study")

    def __init__(
        self,
        host: str,
        port: int,
        called_aet: str,
        calling_aet: str,
        retrieve_mode: str = "c-get",
        move_aet: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.called_aet = called_aet
        self.calling_aet = calling_aet
        if retrieve_mode not in self.VALID_MODES:
            raise ValueError(
                f"Unsupported retrieve_mode {retrieve_mode!r}. "
                f"Expected one of: {', '.join(self.VALID_MODES)}"
            )
        self.retrieve_mode = retrieve_mode
        self.move_aet = calling_aet if move_aet is None else move_aet

    def verify(self) -> bool:
        """Test PACS connectivity via C-ECHO (DICOM Verification SOP Class).

        Returns True if PACS responds to C-ECHO, False otherwise.
        Logs the result at INFO/ERROR level for diagnostics.
        """
        try:
            echo = ctk.ctkDICOMEcho()
        except AttributeError:
            _pacs_log.warning("C-ECHO not available (ctkDICOMEcho missing in this Slicer version)")
            return True  # assume OK if we can't test

        echo.callingAETitle = self.calling_aet
        echo.calledAETitle = self.called_aet
        echo.host = self.host
        echo.port = self.port

        try:
            ok = bool(echo.echo())
        except Exception as e:
            _pacs_log.error(
                f"C-ECHO exception: {e} "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port})"
            )
            return False

        if ok:
            _pacs_log.info(
                f"C-ECHO OK: {self.calling_aet} → {self.host}:{self.port} "
                f"(called={self.called_aet})"
            )
        else:
            _pacs_log.error(
                f"C-ECHO failed: PACS not reachable or rejected association "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port}). "
                f"Check: (1) AE title '{self.calling_aet}' is allowed by the PACS ACL "
                f"with the correct IP of this machine; "
                f"(2) PACS AE title '{self.called_aet}' and address "
                f"{self.host}:{self.port} are correct."
            )
        return ok

    @classmethod
    def from_slicer(cls, server_name: str | None = None) -> PacsHelper:
        """Create PacsHelper from Slicer's configured PACS servers.

        Reads connection parameters from Slicer's DICOM module, so each user's
        local Slicer configuration (including their own calling AE title) is used.

        Args:
            server_name: Optional connection name to select a specific server.
                If None, uses the first query/retrieve-enabled server.

        Raises:
            SlicerHelperError: If no PACS server is configured in Slicer.
        """
        # WORKAROUND: Read PACS servers from QSettings instead of
        # ctkDICOMVisualBrowser API.
        #
        # Slicer has two separate server lists that are NOT synchronized:
        #   1. ctkDICOMVisualBrowser — the new visual DICOM browser widget
        #   2. QSettings (DICOM/ServerNodes/*) — persistent storage used by
        #      the "DICOM Query/Retrieve" dialog and Application Settings
        #
        # Users typically configure PACS servers via Edit > Application Settings
        # > DICOM (the Query/Retrieve dialog), but those changes are only
        # persisted to QSettings. The ctkDICOMVisualBrowser loads its own
        # hard-coded defaults (ExampleHost, MedicalConnections) and does NOT
        # reflect servers added through the settings dialog.
        #
        # Additionally, the Calling AET is stored in TWO places that are NOT
        # synchronized:
        #   - Global: QSettings key "CallingAETitle" (no DICOM/ prefix) —
        #     this is what the user sets in Application Settings > DICOM
        #   - Per-server: DICOM/ServerNodes/{i}["Calling AETitle"] — defaults
        #     to "CTK" and is rarely changed by users
        # We read from the global key first, falling back to per-server.
        #
        # Reading from QSettings directly ensures we see the servers that the
        # user actually configured.
        settings = qt.QSettings()
        count = int(settings.value("DICOM/ServerNodeCount", "0"))

        servers: list[dict[str, Any]] = []
        for i in range(count):
            raw = settings.value(f"DICOM/ServerNodes/{i}")
            if raw is None:
                continue
            try:
                text = raw.data() if hasattr(raw, "data") else str(raw)
                data = json.loads(text)
            except (json.JSONDecodeError, AttributeError, TypeError):
                _pacs_log.warning(f"Skipping malformed DICOM/ServerNodes/{i}")
                continue
            servers.append(data)

        # Qt checkbox tri-state: 0 = Unchecked, 1 = PartiallyChecked, 2 = Checked
        _QT_CHECKED = 2

        server: dict[str, Any] | None = None
        if server_name:
            for s in servers:
                if s.get("Name") == server_name:
                    server = s
                    break
            if server is None:
                raise SlicerHelperError(
                    f"PACS server '{server_name}' not found in Slicer. "
                    f"Configure it in Edit > Application Settings > DICOM."
                )
        else:
            for s in servers:
                if int(s.get("QueryRetrieveCheckState", 0)) == _QT_CHECKED:
                    server = s
                    break
            if server is None and servers:
                server = servers[0]

        if server is None:
            raise SlicerHelperError(
                "No PACS server configured in Slicer. "
                "Add one in Edit > Application Settings > DICOM."
            )

        host = server["Address"]
        port = int(server["Port"])
        called_aet = server["Called AETitle"]

        global_aet = settings.value("CallingAETitle")
        if global_aet:
            calling_aet = str(global_aet)
        else:
            per_server_aet = server.get("Calling AETitle", "SLICER")
            calling_aet = str(per_server_aet)
            _pacs_log.warning(
                f"CallingAETitle not set in Slicer global settings, "
                f"using {'per-server' if per_server_aet != 'SLICER' else 'default'} "
                f"value: {calling_aet}. "
                f"Set it in Edit > Application Settings > DICOM > Calling AE Title."
            )

        retrieve_mode = "c-get" if server.get("Retrieve Protocol", "CGET") == "CGET" else "c-move"

        _pacs_log.info(
            f"Using PACS server: {server.get('Name', 'unknown')} "
            f"({host}:{port}, AET={called_aet}, calling={calling_aet})"
        )

        return cls(
            host=host,
            port=port,
            called_aet=called_aet,
            calling_aet=calling_aet,
            retrieve_mode=retrieve_mode,
            move_aet=calling_aet,
        )

    def _retrieve_study_level(self, retrieve: Any, study_instance_uid: str) -> None:
        """Retrieve using study-level DIMSE operations (getStudy/moveStudy)."""
        prefer_cget = self.retrieve_mode.startswith("c-get")
        if prefer_cget:
            _pacs_log.info(f"C-GET study {study_instance_uid} ...")
            ok = retrieve.getStudy(study_instance_uid)
            if not ok:
                _pacs_log.warning(f"C-GET study failed (ok={ok}), falling back to C-MOVE study")
                retrieve.moveDestinationAETitle = self.move_aet
                ok = retrieve.moveStudy(study_instance_uid)
                if not ok:
                    _pacs_log.error(
                        f"C-MOVE study failed (ok={ok}) for {study_instance_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )
        else:
            _pacs_log.info(f"C-MOVE study {study_instance_uid} ...")
            retrieve.moveDestinationAETitle = self.move_aet
            ok = retrieve.moveStudy(study_instance_uid)
            if not ok:
                _pacs_log.error(
                    f"C-MOVE study failed (ok={ok}) for {study_instance_uid} "
                    f"(calling={self.calling_aet}, called={self.called_aet}, "
                    f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                )

    def _retrieve_series_level(
        self, retrieve: Any, study_instance_uid: str, series_instance_uid: str
    ) -> None:
        """Retrieve using series-level DIMSE operations (getSeries/moveSeries)."""
        prefer_cget = self.retrieve_mode.startswith("c-get")
        if prefer_cget:
            _pacs_log.info(f"C-GET series {series_instance_uid} ...")
            ok = retrieve.getSeries(study_instance_uid, series_instance_uid)
            if not ok or not (
                slicer.dicomDatabase and slicer.dicomDatabase.filesForSeries(series_instance_uid)
            ):
                _pacs_log.warning(
                    f"C-GET failed for series {series_instance_uid} (ok={ok}), "
                    f"falling back to C-MOVE"
                )
                retrieve.moveDestinationAETitle = self.move_aet
                ok = retrieve.moveSeries(study_instance_uid, series_instance_uid)
                if not ok:
                    _pacs_log.error(
                        f"C-MOVE series failed (ok={ok}) for {series_instance_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )
        else:
            _pacs_log.info(f"C-MOVE series {series_instance_uid} ...")
            retrieve.moveDestinationAETitle = self.move_aet
            ok = retrieve.moveSeries(study_instance_uid, series_instance_uid)
            if not ok:
                _pacs_log.error(
                    f"C-MOVE series failed (ok={ok}) for {series_instance_uid} "
                    f"(calling={self.calling_aet}, called={self.called_aet}, "
                    f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                )

    def retrieve_study(self, study_instance_uid: str) -> list[str]:
        """Load a DICOM study into the MRML scene (local-first).

        Checks Slicer's local DICOM database first and loads from there if the
        study already exists. Falls back to C-FIND + retrieve from PACS using
        the configured strategy (c-get, c-get-study, c-move, c-move-study).

        Args:
            study_instance_uid: DICOM Study Instance UID to retrieve.

        Returns:
            List of loaded MRML node IDs.
        """
        # 1. Check local Slicer DICOM database
        db = slicer.dicomDatabase
        local_series: list[str] = db.seriesForStudy(study_instance_uid) if db else []
        if local_series:
            from DICOMLib import DICOMUtils  # type: ignore[import-not-found]

            _pacs_log.info(f"Study {study_instance_uid} found in local DICOM database")
            # list() required: db.seriesForStudy() returns QStringList (tuple in PythonQt),
            # but DICOMUtils.loadSeriesByUID() checks isinstance(x, list)
            loaded: list[str] = DICOMUtils.loadSeriesByUID(list(local_series))
            if loaded:
                return loaded

        # 2. Verify PACS connectivity
        if not self.verify():
            _pacs_log.error("Aborting retrieve — PACS connectivity check failed")
            return []

        # 3. C-FIND: query PACS for the study
        _pacs_log.info(f"C-FIND study {study_instance_uid} ...")
        query = ctk.ctkDICOMQuery()
        query.callingAETitle = self.calling_aet
        query.calledAETitle = self.called_aet
        query.host = self.host
        query.port = self.port
        query.setFilters({"StudyInstanceUID": study_instance_uid})

        temp_db = ctk.ctkDICOMDatabase()
        temp_db.openDatabase("")
        try:
            query.query(temp_db)
        except Exception as e:
            _pacs_log.error(
                f"C-FIND failed for study {study_instance_uid}: {e} "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port})"
            )
            temp_db.closeDatabase()
            return []

        series_to_retrieve: list[str] = [
            series_uid
            for study_uid, series_uid in query.studyAndSeriesInstanceUIDQueried
            if study_uid == study_instance_uid
        ]
        _pacs_log.info(f"C-FIND returned {len(series_to_retrieve)} series")
        if not series_to_retrieve:
            _pacs_log.warning(
                f"C-FIND returned 0 series for study {study_instance_uid} — "
                f"study may not exist on PACS or association was rejected"
            )
            temp_db.closeDatabase()
            return []

        # 4. Retrieve into Slicer DICOM database using configured strategy
        retrieve = ctk.ctkDICOMRetrieve()
        retrieve.callingAETitle = self.calling_aet
        retrieve.calledAETitle = self.called_aet
        retrieve.host = self.host
        retrieve.port = self.port
        retrieve.setDatabase(slicer.dicomDatabase)

        _pacs_log.info(
            f"DIMSE retrieve: {self.calling_aet} → {self.host}:{self.port} "
            f"(called={self.called_aet}, mode={self.retrieve_mode}, move_dest={self.move_aet})"
        )

        retrieved_series_uids: list[str] = []
        study_level = self.retrieve_mode.endswith("-study")

        if study_level:
            # Study-level: single getStudy/moveStudy call retrieves all series at once
            self._retrieve_study_level(retrieve, study_instance_uid)
            for series_uid in series_to_retrieve:
                files = db.filesForSeries(series_uid) if db else ()
                if files:
                    _pacs_log.info(f"Retrieved {len(files)} files for series {series_uid}")
                    retrieved_series_uids.append(series_uid)
                else:
                    _pacs_log.error(
                        f"Retrieve failed: 0 files for series {series_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )
        else:
            # Series-level: retrieve each series individually
            for series_uid in series_to_retrieve:
                self._retrieve_series_level(retrieve, study_instance_uid, series_uid)
                files = db.filesForSeries(series_uid) if db else ()
                if files:
                    _pacs_log.info(f"Retrieved {len(files)} files for series {series_uid}")
                    retrieved_series_uids.append(series_uid)
                else:
                    _pacs_log.error(
                        f"Retrieve failed: 0 files for series {series_uid} "
                        f"(calling={self.calling_aet}, called={self.called_aet}, "
                        f"host={self.host}:{self.port}, move_dest={self.move_aet})"
                    )

        # 5. Load ONLY the retrieved series into the MRML scene
        from DICOMLib import DICOMUtils  # type: ignore[import-not-found]

        loaded_node_ids: list[str] = DICOMUtils.loadSeriesByUID(retrieved_series_uids)

        temp_db.closeDatabase()
        return loaded_node_ids or []

    def retrieve_series(self, study_instance_uid: str, series_instance_uid: str) -> list[str]:
        """Load a single DICOM series into the MRML scene (local-first).

        Checks Slicer's local DICOM database first and loads from there if the
        series already exists. Falls back to retrieve from PACS using the
        configured strategy. With ``-study`` modes, retrieves the entire study
        but only loads the requested series.

        Args:
            study_instance_uid: DICOM Study Instance UID.
            series_instance_uid: DICOM Series Instance UID to retrieve.

        Returns:
            List of loaded MRML node IDs.
        """
        # 1. Check local Slicer DICOM database
        db = slicer.dicomDatabase
        if db and db.filesForSeries(series_instance_uid):
            from DICOMLib import DICOMUtils  # type: ignore[import-not-found]

            _pacs_log.info(f"Series {series_instance_uid} found in local DICOM database")
            loaded: list[str] = DICOMUtils.loadSeriesByUID([series_instance_uid])
            if loaded:
                return loaded

        # 2. Verify PACS connectivity
        if not self.verify():
            _pacs_log.error("Aborting retrieve — PACS connectivity check failed")
            return []

        # 3. Retrieve from PACS using configured strategy
        retrieve = ctk.ctkDICOMRetrieve()
        retrieve.callingAETitle = self.calling_aet
        retrieve.calledAETitle = self.called_aet
        retrieve.host = self.host
        retrieve.port = self.port
        retrieve.setDatabase(slicer.dicomDatabase)

        _pacs_log.info(
            f"DIMSE retrieve: {self.calling_aet} → {self.host}:{self.port} "
            f"(called={self.called_aet}, mode={self.retrieve_mode}, move_dest={self.move_aet})"
        )

        if self.retrieve_mode.endswith("-study"):
            # Study-level retrieval — downloads entire study, loads requested series
            self._retrieve_study_level(retrieve, study_instance_uid)
        else:
            self._retrieve_series_level(retrieve, study_instance_uid, series_instance_uid)

        # Verify files arrived in the database
        files_after = db.filesForSeries(series_instance_uid) if db else ()
        if not files_after:
            _pacs_log.error(
                f"Retrieve failed: 0 files for series {series_instance_uid} "
                f"(calling={self.calling_aet}, called={self.called_aet}, "
                f"host={self.host}:{self.port}, move_dest={self.move_aet})"
            )
            return []

        _pacs_log.info(f"Retrieved {len(files_after)} files for series {series_instance_uid}")

        from DICOMLib import DICOMUtils  # type: ignore[import-not-found]

        loaded_node_ids: list[str] = DICOMUtils.loadSeriesByUID([series_instance_uid])
        return loaded_node_ids or []


class SlicerHelper:
    """DSL for concise 3D Slicer workspace setup.

    Provides a high-level API that wraps verbose Slicer Python calls into
    short, chainable methods. Designed to run inside the 3D Slicer environment.
    """

    def __init__(self, working_folder: str) -> None:
        """Reset views, clear scene, and set root directory.

        Order matters: slice view composite nodes are detached BEFORE
        ``mrmlScene.Clear(0)`` to avoid VTK "Input port 0 has 0 connections"
        warnings that would otherwise flood the Qt event loop and block
        subsequent HTTP requests. Detaching upstream removes the stale
        BackgroundVolumeID/ForegroundVolumeID/LabelVolumeID references
        before Clear deletes the volumes, so no warnings are ever queued
        and an explicit ``processEvents()`` drain is unnecessary — which
        also avoids Qt re-entrancy if this runs inside an HTTP event
        handler.

        Args:
            working_folder: Absolute path to the working directory.
        """
        self.working_folder = working_folder
        self._scene = slicer.mrmlScene
        self._layout_manager = slicer.app.layoutManager()
        self._image_node: Any = None
        self._editor_widget: Any = None
        self._observer_tags: list[tuple[Any, int]] = []
        self._shortcuts: list[Any] = []

        # 1. Detach slice view composite nodes first (avoid dangling refs
        # to volumes that Clear(0) is about to delete).
        for name in ("Red", "Yellow", "Green"):
            widget = self._layout_manager.sliceWidget(name)
            if widget is None:
                continue
            composite = widget.mrmlSliceCompositeNode()
            composite.SetBackgroundVolumeID(None)
            composite.SetForegroundVolumeID(None)
            composite.SetLabelVolumeID(None)

        # 2. Clear scene and set working folder.
        self._scene.Clear(0)
        self._scene.SetRootDirectory(working_folder)

    def cleanup(self) -> None:
        """Remove all observers and shortcuts registered by this helper."""
        for node, tag in self._observer_tags:
            with contextlib.suppress(Exception):
                node.RemoveObserver(tag)
        self._observer_tags.clear()

        for shortcut in self._shortcuts:
            shortcut.setParent(None)
            shortcut.deleteLater()
        self._shortcuts.clear()

    def load_volume(
        self,
        path: str,
        window: tuple[float, float] | None = None,
    ) -> Any:
        """Load volume file (NRRD/NIfTI/DICOM) and optionally set window/level.

        Args:
            path: File path relative to working_folder, or absolute.
            window: Optional (min, max) window level values.

        Returns:
            The loaded image node.
        """
        full_path = path if os.path.isabs(path) else os.path.join(self.working_folder, path)
        self._image_node = slicer.util.loadVolume(full_path)

        if window is not None:
            display = self._image_node.GetScalarVolumeDisplayNode()
            display.AutoWindowLevelOff()
            display.SetWindowLevelMinMax(window[0], window[1])

        return self._image_node

    def create_segmentation(self, name: str) -> SegmentationBuilder:
        """Create an empty segmentation node.

        Args:
            name: Display name for the segmentation.

        Returns:
            SegmentationBuilder for fluent segment addition.
        """
        node = self._scene.AddNewNodeByClass("vtkMRMLSegmentationNode", name)
        node.CreateDefaultDisplayNodes()
        if self._image_node is not None:
            node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)
        return SegmentationBuilder(node, self._image_node)

    def load_segmentation(self, path: str, name: str | None = None) -> Any:
        """Load existing segmentation from file.

        Args:
            path: File path relative to working_folder, or absolute.
            name: Optional display name. Defaults to filename stem.

        Returns:
            The loaded segmentation node.
        """
        full_path = path if os.path.isabs(path) else os.path.join(self.working_folder, path)
        seg_node = slicer.util.loadSegmentation(full_path)

        if name is not None:
            seg_node.SetName(name)

        if self._image_node is not None:
            seg_node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)

        seg_node.CreateDefaultDisplayNodes()
        return seg_node

    def set_source_volume(self, node: Any) -> None:
        """Set the source volume node for segmentation editing.

        Args:
            node: A vtkMRMLScalarVolumeNode to use as source volume.
        """
        self._image_node = node

    def set_segmentation_visibility(
        self,
        segmentation: SegmentationBuilder | Any,
        visible: bool,
    ) -> None:
        """Show or hide a segmentation in all views.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            visible: Whether the segmentation should be visible.
        """
        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        display = node.GetDisplayNode()
        if display is not None:
            display.SetVisibility(int(visible))

    def configure_segment_display(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
        *,
        color: tuple[float, float, float] | None = None,
        fill_opacity: float | None = None,
        outline_opacity: float | None = None,
        outline_thickness: int | None = None,
    ) -> None:
        """Configure display properties for a single segment.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            segment_name: Name of the segment to configure.
            color: RGB color tuple (0-1 range). None to keep current.
            fill_opacity: 2D fill opacity (0.0 = transparent, 1.0 = opaque).
            outline_opacity: 2D outline opacity (0.0 = hidden, 1.0 = opaque).
            outline_thickness: Slice intersection line thickness in pixels
                (applies to the whole segmentation display node).
        """
        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        vtk_seg = node.GetSegmentation()
        display = node.GetDisplayNode()

        seg_id = _find_segment_id(vtk_seg, segment_name)
        if seg_id is None:
            return

        if color is not None:
            vtk_seg.GetSegment(seg_id).SetColor(*color)

        if display is not None:
            if fill_opacity is not None:
                display.SetSegmentOpacity2DFill(seg_id, fill_opacity)
            if outline_opacity is not None:
                display.SetSegmentOpacity2DOutline(seg_id, outline_opacity)
            if outline_thickness is not None:
                display.SetSliceIntersectionThickness(outline_thickness)

    def setup_editor(
        self,
        segmentation: SegmentationBuilder | Any,
        effect: EditorEffectName = "Paint",
        brush_size: float = 20.0,
        threshold: tuple[float, float] | None = None,
        sphere_brush: bool = True,
        source_volume: Any = None,
    ) -> None:
        """Open SegmentEditor and configure tools.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            effect: Effect name to activate.
            brush_size: Brush diameter in mm.
            threshold: Optional (min, max) threshold values for Threshold effect.
            sphere_brush: Use spherical brush (True) or circular (False).
            source_volume: Override source volume node. Falls back to ``_image_node``.
        """
        seg_node = (
            segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        )

        slicer.util.selectModule("SegmentEditor")
        self._editor_widget = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        self._editor_widget.setSegmentationNode(seg_node)

        volume = source_volume if source_volume is not None else self._image_node
        if volume is not None:
            self._editor_widget.setSourceVolumeNode(volume)

        # Configure effect
        self._editor_widget.setActiveEffectByName(effect)
        active_effect = self._editor_widget.activeEffect()

        if active_effect is not None:
            if effect in ("Paint", "Erase"):
                active_effect.setCommonParameter("BrushDiameterIsRelative", 0)
                active_effect.setCommonParameter("BrushAbsoluteDiameter", brush_size)
                active_effect.setCommonParameter("BrushSphere", int(sphere_brush))
            elif effect == "Threshold" and threshold is not None:
                active_effect.setParameter("MinimumThreshold", threshold[0])
                active_effect.setParameter("MaximumThreshold", threshold[1])
                active_effect.self().onUseForPaint()
            elif effect == "Islands":
                active_effect.setParameter("Operation", "ADD_SELECTED_ISLAND")

    def set_layout(self, layout: str) -> None:
        """Set view layout.

        Args:
            layout: One of 'axial', 'sagittal', 'coronal', 'four_up'.
        """
        attr_name = LAYOUT_MAP.get(layout)
        if attr_name is None:
            raise ValueError(f"Unknown layout '{layout}'. Use: {list(LAYOUT_MAP.keys())}")
        layout_id = getattr(slicer.vtkMRMLLayoutNode, attr_name)
        self._layout_manager.setLayout(layout_id)

    def annotate(
        self,
        text: str,
        position: str = "upper_right",
        color: tuple[float, float, float] = (1.0, 0.0, 0.0),
    ) -> None:
        """Add text annotation to the red slice view.

        Args:
            text: Annotation text to display.
            position: VTK corner position name (e.g. 'upper_right', 'upper_left').
            color: RGB color tuple (0-1 range).
        """
        position_map = {
            "upper_right": vtk.vtkCornerAnnotation.UpperRight,
            "upper_left": vtk.vtkCornerAnnotation.UpperLeft,
            "lower_right": vtk.vtkCornerAnnotation.LowerRight,
            "lower_left": vtk.vtkCornerAnnotation.LowerLeft,
        }
        vtk_pos = position_map.get(position, vtk.vtkCornerAnnotation.UpperRight)

        view = self._layout_manager.sliceWidget("Red").sliceView()
        view.cornerAnnotation().SetText(vtk_pos, text)
        view.cornerAnnotation().GetTextProperty().SetColor(*color)
        view.forceRender()

    def configure_slab(self, thickness: float = 10.0, reconstruction_type: int = 1) -> None:
        """Enable slab reconstruction on axial (Red) view.

        Args:
            thickness: Slab thickness in mm.
            reconstruction_type: 0=Max, 1=Mean, 2=Sum.
        """
        slice_node = self._scene.GetNodeByID("vtkMRMLSliceNodeRed")
        slice_node.SlabReconstructionEnabledOn()
        slice_node.SetSlabReconstructionThickness(thickness)
        slice_node.SetSlabReconstructionType(reconstruction_type)

    def setup_edit_mask(self, mask_path: str, segment_id: str = "Segment_1") -> None:
        """Load edit mask segmentation and restrict editing to mask region.

        Args:
            mask_path: Path to the mask segmentation file.
            segment_id: Segment ID within the mask to use as editable area.
        """
        mask_node = self.load_segmentation(mask_path, name="EditMask")

        if self._editor_widget is not None:
            self._editor_widget.setMaskSegmentationNode(mask_node)
            self._editor_widget.setMaskSegmentID(segment_id)
            self._editor_widget.setMaskMode(
                slicer.vtkMRMLSegmentEditorNode.PaintAllowedInsideSingleSegment
            )

    def add_view_shortcuts(self) -> None:
        """Add standard keyboard shortcuts: a/s/c for axial/sagittal/coronal."""
        self.add_shortcuts(
            [
                ("a", "axial"),
                ("s", "sagittal"),
                ("c", "coronal"),
            ]
        )

    def add_shortcuts(self, shortcuts: list[tuple[str, str]]) -> None:
        """Add custom keyboard shortcuts.

        Args:
            shortcuts: List of (key, layout_or_code) tuples. If the value is a
                       known layout name, sets that layout. Otherwise the value
                       is treated as Python code to exec.
        """
        main_window = slicer.util.mainWindow()
        for key, action in shortcuts:
            shortcut = qt.QShortcut(main_window)
            shortcut.setKey(qt.QKeySequence(key))
            if action in LAYOUT_MAP:
                layout_attr = LAYOUT_MAP[action]
                layout_id = getattr(slicer.vtkMRMLLayoutNode, layout_attr)
                shortcut.connect(
                    "activated()",
                    lambda lid=layout_id: self._layout_manager.setLayout(lid),
                )
            else:
                shortcut.connect("activated()", lambda code=action: exec(code))
            self._shortcuts.append(shortcut)

    def load_study_from_pacs(
        self,
        study_instance_uid: str,
        *,
        server_name: str | None = None,
        raise_on_empty: bool = True,
    ) -> list[str]:
        """Load a DICOM study from PACS into the current scene.

        Uses PACS connection params from Clarinet settings (injected via context
        variables). Falls back to Slicer's DICOM module config if context is absent.

        Args:
            study_instance_uid: DICOM Study Instance UID to retrieve.
            server_name: Optional PACS server name configured in Slicer
                (only used in fallback mode).
            raise_on_empty: If True (default), raise SlicerHelperError when no
                DICOM nodes are loaded. Set to False for optional/fallback loads.

        Returns:
            List of loaded MRML node IDs.

        Raises:
            SlicerHelperError: If no nodes were loaded and raise_on_empty is True.
        """
        pacs = _get_pacs_helper(server_name)
        node_ids = pacs.retrieve_study(study_instance_uid)

        # Auto-set first scalar volume as source for Segment Editor
        for nid in node_ids or []:
            node = self._scene.GetNodeByID(nid)
            if node is not None and node.IsA("vtkMRMLScalarVolumeNode"):
                self._image_node = node
                break

        node_ids = node_ids or []
        if raise_on_empty and not node_ids:
            raise SlicerHelperError(
                f"No DICOM nodes loaded for study '{study_instance_uid}'. "
                f"Check PACS configuration in Edit > Application Settings > DICOM."
            )
        return node_ids

    def load_series_from_pacs(
        self,
        study_instance_uid: str,
        series_instance_uid: str,
        *,
        server_name: str | None = None,
        raise_on_empty: bool = True,
    ) -> list[str]:
        """Load a single DICOM series from PACS into the current scene.

        Uses PACS connection params from Clarinet settings (injected via context
        variables). Falls back to Slicer's DICOM module config if context is absent.

        Args:
            study_instance_uid: DICOM Study Instance UID.
            series_instance_uid: DICOM Series Instance UID to retrieve.
            server_name: Optional PACS server name configured in Slicer
                (only used in fallback mode).
            raise_on_empty: If True (default), raise SlicerHelperError when no
                DICOM nodes are loaded. Set to False for optional/fallback loads.

        Returns:
            List of loaded MRML node IDs.

        Raises:
            SlicerHelperError: If no nodes were loaded and raise_on_empty is True.
        """
        pacs = _get_pacs_helper(server_name)
        node_ids = pacs.retrieve_series(study_instance_uid, series_instance_uid)

        # Auto-set first scalar volume as source for Segment Editor
        for nid in node_ids or []:
            node = self._scene.GetNodeByID(nid)
            if node is not None and node.IsA("vtkMRMLScalarVolumeNode"):
                self._image_node = node
                break

        node_ids = node_ids or []
        if raise_on_empty and not node_ids:
            raise SlicerHelperError(
                f"No DICOM nodes loaded for series '{series_instance_uid}' "
                f"(study '{study_instance_uid}'). "
                f"Check PACS configuration in Edit > Application Settings > DICOM."
            )
        return node_ids

    def download_series_zip(
        self,
        study_uid: str,
        series_uid: str,
        server_url: str,
        auth_cookie: str,
    ) -> str:
        """Download DICOM series ZIP from Clarinet, extract, import into Slicer DB.

        Alternative to DIMSE retrieval — downloads via HTTP from Clarinet's
        DICOMweb cache endpoint.

        Args:
            study_uid: DICOM Study Instance UID.
            series_uid: DICOM Series Instance UID.
            server_url: Clarinet API base URL (e.g. "http://host:8000").
            auth_cookie: Cookie header value (e.g. "clarinet_session=token").

        Returns:
            Path to the directory containing extracted DICOM files.
        """
        import shutil
        import tempfile
        import urllib.request
        import zipfile

        url = f"{server_url}/dicom-web/studies/{study_uid}/series/{series_uid}/archive"
        req = urllib.request.Request(url)
        req.add_header("Cookie", auth_cookie)

        extract_dir = os.path.join(self.working_folder, "_dicom_download", series_uid)
        os.makedirs(extract_dir, exist_ok=True)

        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)  # noqa: SIM115
        tmp_path = tmp.name
        tmp.close()
        try:
            with urllib.request.urlopen(req) as resp, open(tmp_path, "wb") as f:
                shutil.copyfileobj(resp, f)
            with zipfile.ZipFile(tmp_path) as zf:
                zf.extractall(extract_dir)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Import into Slicer DICOM database (if running inside Slicer)
        try:
            from DICOMLib import DICOMUtils  # type: ignore[import-not-found]

            DICOMUtils.importDicom(extract_dir)
        except ImportError:
            pass

        return extract_dir

    def get_segment_names(self, segmentation: SegmentationBuilder | Any) -> list[str]:
        """Get ordered list of segment names from a segmentation node.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.

        Returns:
            List of segment names in index order.
        """
        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        vtk_seg = node.GetSegmentation()
        names: list[str] = []
        for i in range(vtk_seg.GetNumberOfSegments()):
            seg_id = vtk_seg.GetNthSegmentID(i)
            names.append(vtk_seg.GetSegment(seg_id).GetName())
        return names

    def get_segment_centroid(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
    ) -> tuple[float, float, float] | None:
        """Compute the RAS centroid of a named segment via per-segment labelmap.

        Extracts a per-segment copy of the binary labelmap using the MRML
        node-level API (not the shared representation from the segment
        object). Computes the tight bounding-box center from actual non-zero
        voxels using numpy, which works correctly regardless of shared
        labelmaps or missing extent metadata.

        Safe to call from observer callbacks — no event processing, no
        re-entry risk.

        .. note:: **VTK shared-labelmap pitfall (Slicer 5.0+)**

           In modern Slicer, multiple segments share a single
           ``vtkOrientedImageData`` (shared labelmap). The intuitive API —
           ``segment.GetRepresentation("Binary labelmap")`` — returns this
           *shared* object, whose extent covers the **entire volume** (all
           segments combined). Computing the bounding-box center from the
           shared labelmap yields the same point (volume center) for every
           segment, making ``JumpSlice`` appear to do nothing.

           The fix is ``node.GetBinaryLabelmapRepresentation(seg_id, out)`` —
           the MRML-node-level API that extracts a **per-segment copy**.
           This is the same API used internally by
           ``slicer.util.arrayFromSegmentBinaryLabelmap()``.

        .. note:: **np.nonzero() vs np.any() — performance**

           The naive approach (``np.nonzero(arr > 0)``) allocates three huge
           arrays containing coordinates of ALL non-zero voxels. For a large
           segment in a 512x512x300 volume this means millions of int64
           entries per array — hundreds of MB of allocation per call.

           Instead we use ``np.any(mask, axis=(...))`` to project the 3D
           boolean mask down to three 1D arrays (length ~512 each). Then
           ``np.where()`` on those tiny arrays gives min/max indices per axis.
           The centroid is the midpoint of the tight bounding box — identical
           result, ~10-50x faster, negligible memory.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            segment_name: Name of the segment to find.

        Returns:
            (R, A, S) centroid tuple, or None if the segment is empty.
        """
        import numpy as np
        import vtkSegmentationCorePython as vtkSegCore  # type: ignore[import-not-found]
        from vtk.util.numpy_support import vtk_to_numpy  # type: ignore[import-not-found]

        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        vtk_seg = node.GetSegmentation()

        seg_id = _find_segment_id(vtk_seg, segment_name)
        if seg_id is None:
            return None

        # Extract per-segment labelmap via node-level API.
        # DO NOT use segment.GetRepresentation("Binary labelmap") — it
        # returns the shared labelmap (same extent for all segments).
        # See docstring "VTK shared-labelmap pitfall" above.
        labelmap = vtkSegCore.vtkOrientedImageData()
        node.GetBinaryLabelmapRepresentation(seg_id, labelmap)

        # extent = (xmin, xmax, ymin, ymax, zmin, zmax); xmin > xmax means empty.
        extent = labelmap.GetExtent()
        if extent[0] > extent[1]:
            return None

        scalars = labelmap.GetPointData().GetScalars()
        if scalars is None:
            return None

        # VTK stores scalars in Fortran-contiguous order (i varies fastest),
        # but numpy reshape expects (slowest, ..., fastest) = (k, j, i).
        dims = labelmap.GetDimensions()
        arr = vtk_to_numpy(scalars).reshape(dims[2], dims[1], dims[0])

        mask = arr > 0

        # Axis projections: collapse 3D mask to 1D per axis.
        # np.any(mask, axis=(0,1)) keeps the i-axis, collapsing k and j.
        # Result: three boolean arrays of length ~dims[0/1/2] (~512 each).
        # See docstring "np.nonzero() vs np.any()" above.
        i_any = np.any(mask, axis=(0, 1))  # length dims[0]
        j_any = np.any(mask, axis=(0, 2))  # length dims[1]
        k_any = np.any(mask, axis=(1, 2))  # length dims[2]

        i_idx = np.where(i_any)[0]
        if len(i_idx) == 0:
            return None
        j_idx = np.where(j_any)[0]
        k_idx = np.where(k_any)[0]

        # Tight bounding-box center in array coords (k, j, i) → IJK.
        # extent offsets translate from local labelmap coords to volume IJK.
        ci = (float(i_idx[0]) + float(i_idx[-1])) / 2.0 + extent[0]
        cj = (float(j_idx[0]) + float(j_idx[-1])) / 2.0 + extent[2]
        ck = (float(k_idx[0]) + float(k_idx[-1])) / 2.0 + extent[4]

        # IJK → RAS via the labelmap's own image-to-world matrix.
        mat = vtk.vtkMatrix4x4()
        labelmap.GetImageToWorldMatrix(mat)
        ras = mat.MultiplyPoint([ci, cj, ck, 1.0])
        return (ras[0], ras[1], ras[2])

    def _local_to_world_centroid(
        self,
        segmentation: SegmentationBuilder | Any,
        segment_name: str,
    ) -> tuple[float, float, float] | None:
        """Convert a segment centroid from local RAS to world RAS.

        ``get_segment_centroid`` returns coordinates in the labelmap's own
        image-to-world space (local RAS). If the segmentation node has a
        parent MRML transform (e.g. an alignment transform), this method
        applies it to produce world RAS coordinates suitable for
        ``JumpSlice``.

        Args:
            segmentation: Segmentation node or SegmentationBuilder.
            segment_name: Name of the segment.

        Returns:
            World RAS centroid or None if the segment is empty.
        """
        local = self.get_segment_centroid(segmentation, segment_name)
        if local is None:
            return None

        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        parent_tf = node.GetParentTransformNode()
        if parent_tf is None:
            return local

        mat = vtk.vtkMatrix4x4()
        parent_tf.GetMatrixTransformToWorld(mat)
        world = mat.MultiplyPoint([local[0], local[1], local[2], 1.0])
        return (world[0], world[1], world[2])

    def copy_segments(
        self,
        source_seg: SegmentationBuilder | Any,
        target_seg: SegmentationBuilder | Any,
        segment_names: list[str] | None = None,
        empty: bool = False,
    ) -> None:
        """Copy segments from one segmentation to another.

        Args:
            source_seg: Source segmentation (SegmentationBuilder or node).
            target_seg: Target segmentation (SegmentationBuilder or node).
            segment_names: Optional list of segment names to copy. Copies all if None.
            empty: If True, copy only segment metadata (name + color) without data.
        """
        source_node = source_seg.node if isinstance(source_seg, SegmentationBuilder) else source_seg
        target_node = target_seg.node if isinstance(target_seg, SegmentationBuilder) else target_seg
        source_vtk_seg = source_node.GetSegmentation()
        target_vtk_seg = target_node.GetSegmentation()

        for i in range(source_vtk_seg.GetNumberOfSegments()):
            seg_id = source_vtk_seg.GetNthSegmentID(i)
            segment = source_vtk_seg.GetSegment(seg_id)
            name = segment.GetName()

            if segment_names is not None and name not in segment_names:
                continue

            if empty:
                color = segment.GetColor()
                target_vtk_seg.AddEmptySegment(name, name, color)
            else:
                target_vtk_seg.CopySegmentFromSegmentation(source_vtk_seg, seg_id)

    def sync_segments(
        self,
        source_seg: SegmentationBuilder | Any,
        target_seg: SegmentationBuilder | Any,
        empty: bool = False,
    ) -> list[str]:
        """Copy segments from source that are missing in target (by name).

        Args:
            source_seg: Source segmentation with reference segments.
            target_seg: Target segmentation to sync into.
            empty: If True, copy only metadata (name + color) without data.

        Returns:
            List of segment names that were added, in source order.
        """
        source_names = self.get_segment_names(source_seg)
        existing = set(self.get_segment_names(target_seg))
        missing = [name for name in source_names if name not in existing]
        if missing:
            self.copy_segments(source_seg, target_seg, segment_names=missing, empty=empty)
        return missing

    def rename_segments(
        self,
        segmentation: SegmentationBuilder | Any,
        prefix: str = "NEW",
        color: tuple[float, float, float] | None = None,
        start_from: int = 1,
    ) -> int:
        """Rename all segments to {prefix}_{N} with optional color.

        Args:
            segmentation: Segmentation node or SegmentationBuilder.
            prefix: Name prefix for segments.
            color: Optional RGB color tuple (0.0-1.0) to apply to all segments.
            start_from: Starting number for renaming.

        Returns:
            Number of renamed segments.
        """
        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        vtk_seg = node.GetSegmentation()
        count = vtk_seg.GetNumberOfSegments()
        for i in range(count):
            sid = vtk_seg.GetNthSegmentID(i)
            segment = vtk_seg.GetSegment(sid)
            segment.SetName(f"{prefix}_{start_from + i}")
            if color is not None:
                segment.SetColor(*color)
        return int(count)

    def auto_number_segment(
        self,
        segmentation: SegmentationBuilder | Any,
        prefix: str = "ROI",
        start_from: int | None = None,
    ) -> int:
        """Add a new numbered segment with the next available number.

        Parses existing segment names matching ``{prefix}_{N}`` (or just ``N``
        when *prefix* is empty) to find the highest number, then creates the
        next one.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            prefix: Name prefix for numbered segments. Empty string produces
                plain numeric names (``"1"``, ``"2"``, …).
            start_from: Force a specific number instead of auto-detecting.

        Returns:
            The number assigned to the new segment.
        """
        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        vtk_seg = node.GetSegmentation()
        names = self.get_segment_names(segmentation)

        if start_from is not None:
            next_num = start_from
        else:
            max_num = 0
            if prefix:
                pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
            else:
                pattern = re.compile(r"^(\d+)$")
            for name in names:
                m = pattern.match(name)
                if m:
                    max_num = max(max_num, int(m.group(1)))
            next_num = max_num + 1

        seg_name = f"{prefix}_{next_num}" if prefix else str(next_num)
        vtk_seg.AddEmptySegment(seg_name, seg_name)
        return next_num

    def subtract_segmentations(
        self,
        seg_a: SegmentationBuilder | Any,
        seg_b: SegmentationBuilder | Any,
        output_name: str | None = None,
        max_overlap: int = 0,
        max_overlap_ratio: float | None = None,
    ) -> Any:
        """ROI-level subtraction: remove segments from seg_a that overlap with seg_b.

        For each segment in seg_a, counts voxel overlap with the merged seg_b
        labelmap. Segments exceeding overlap thresholds are removed.

        Args:
            seg_a: Segmentation to subtract from (SegmentationBuilder or node).
            seg_b: Segmentation to subtract (SegmentationBuilder or node).
            output_name: If set, create a new node with surviving segments instead
                         of modifying seg_a in-place.
            max_overlap: Maximum allowed overlap voxel count (segments with more are removed).
            max_overlap_ratio: Maximum allowed overlap ratio (overlap/total). Both
                               thresholds must be exceeded for removal when set.

        Returns:
            The output segmentation node (new node if output_name, else seg_a node).
        """
        import numpy as np  # type: ignore[import-not-found]

        node_a = seg_a.node if isinstance(seg_a, SegmentationBuilder) else seg_a
        node_b = seg_b.node if isinstance(seg_b, SegmentationBuilder) else seg_b

        # Align both to the same reference grid
        if self._image_node is not None:
            node_a.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)
            node_b.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)

        seg_logic = slicer.modules.segmentations.logic()

        # Export both with mode 0 (reference geometry extent) → same shape
        labelmap_b = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "_sub_b")
        seg_logic.ExportAllSegmentsToLabelmapNode(node_b, labelmap_b, 0)
        arr_b = slicer.util.arrayFromVolume(labelmap_b)

        labelmap_a = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "_sub_a")
        seg_logic.ExportAllSegmentsToLabelmapNode(node_a, labelmap_a, 0)
        arr_a = slicer.util.arrayFromVolume(labelmap_a)

        vtk_seg_a = node_a.GetSegmentation()
        segments_to_remove: list[str] = []

        for i in range(vtk_seg_a.GetNumberOfSegments()):
            seg_id = vtk_seg_a.GetNthSegmentID(i)
            label_value = i + 1  # merged labelmap: segment 0 → label 1

            mask_a = arr_a == label_value
            total = int(np.sum(mask_a))
            if total == 0:
                continue

            overlap = int(np.sum(mask_a & (arr_b > 0)))

            remove = overlap > max_overlap
            if max_overlap_ratio is not None:
                remove = remove and (overlap / total > max_overlap_ratio)
            if remove:
                segments_to_remove.append(seg_id)

        slicer.mrmlScene.RemoveNode(labelmap_a)
        slicer.mrmlScene.RemoveNode(labelmap_b)

        if output_name is not None:
            # Create new segmentation with surviving segments
            output_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", output_name)
            output_node.CreateDefaultDisplayNodes()
            if self._image_node is not None:
                output_node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)
            output_vtk = output_node.GetSegmentation()
            for i in range(vtk_seg_a.GetNumberOfSegments()):
                seg_id = vtk_seg_a.GetNthSegmentID(i)
                if seg_id not in segments_to_remove:
                    output_vtk.CopySegmentFromSegmentation(vtk_seg_a, seg_id)
            return output_node

        # In-place removal
        for seg_id in segments_to_remove:
            vtk_seg_a.RemoveSegment(seg_id)
        return node_a

    def binarize_and_split_islands(
        self,
        segmentation: SegmentationBuilder | Any,
        output_name: str = "_BinarizedIslands",
        min_island_size: int = 1,
    ) -> Any:
        """Binarize all segments into one mask, then split into connected components.

        Merges all segments into a single binary labelmap (any label > 0),
        then uses the Islands effect to split connected components into
        individual segments.

        Args:
            segmentation: Input segmentation (SegmentationBuilder or node).
            output_name: Name for the output segmentation node.
            min_island_size: Minimum island size in voxels (smaller removed).

        Returns:
            A new vtkMRMLSegmentationNode with one segment per connected component.
        """
        import numpy as np  # type: ignore[import-not-found]

        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation

        if self._image_node is not None:
            node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)

        seg_logic = slicer.modules.segmentations.logic()

        # Phase A — merge all segments into a single binary labelmap
        labelmap = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "_bin_tmp")
        seg_logic.ExportAllSegmentsToLabelmapNode(node, labelmap, 0)
        arr = slicer.util.arrayFromVolume(labelmap)
        arr_binary = (arr > 0).astype(np.uint8)
        slicer.util.updateVolumeFromArray(labelmap, arr_binary)

        output_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentationNode", output_name)
        output_node.CreateDefaultDisplayNodes()
        if self._image_node is not None:
            output_node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)
        seg_logic.ImportLabelmapToSegmentationNode(labelmap, output_node)
        slicer.mrmlScene.RemoveNode(labelmap)

        # Phase B — split islands via Segment Editor Islands effect
        merged_seg_id = output_node.GetSegmentation().GetNthSegmentID(0)

        editor_node = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLSegmentEditorNode", "_bin_editor")
        editor_node.SetSelectedSegmentID(merged_seg_id)

        widget = slicer.qMRMLSegmentEditorWidget()
        widget.setMRMLScene(slicer.mrmlScene)
        widget.setMRMLSegmentEditorNode(editor_node)
        widget.setSegmentationNode(output_node)
        if self._image_node is not None:
            widget.setSourceVolumeNode(self._image_node)

        widget.setActiveEffectByName("Islands")
        effect = widget.activeEffect()
        effect.setParameter("Operation", "SPLIT_ISLANDS_TO_SEGMENTS")
        effect.setParameter("MinimumSize", str(min_island_size))
        effect.self().onApply()

        slicer.mrmlScene.RemoveNode(editor_node)
        widget.deleteLater()

        return output_node

    def merge_as_pool(
        self,
        source_seg: SegmentationBuilder | Any,
        target_seg: SegmentationBuilder | Any,
        pool_name: str = "_pool",
        color: tuple[float, float, float] = (0.5, 0.5, 0.5),
    ) -> None:
        """Merge all source segments into a single binary segment in the target.

        Exports all source segments to a labelmap, binarizes (any label > 0),
        and imports the result into the target segmentation as a single segment.
        Useful for cross-segmentation Islands workflow: the pool segment becomes
        visible in the target's merged labelmap, allowing ADD_SELECTED_ISLAND
        to pick islands from it.

        Args:
            source_seg: Source segmentation (SegmentationBuilder or node).
            target_seg: Target segmentation (SegmentationBuilder or node).
            pool_name: Name for the pool segment in the target.
            color: RGB color tuple (0-1 range) for the pool segment.
        """
        import numpy as np  # type: ignore[import-not-found]

        source_node = source_seg.node if isinstance(source_seg, SegmentationBuilder) else source_seg
        target_node = target_seg.node if isinstance(target_seg, SegmentationBuilder) else target_seg

        if self._image_node is not None:
            source_node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)
            target_node.SetReferenceImageGeometryParameterFromVolumeNode(self._image_node)

        seg_logic = slicer.modules.segmentations.logic()

        # Export source → binarize
        labelmap = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "_pool_tmp")
        seg_logic.ExportAllSegmentsToLabelmapNode(source_node, labelmap, 0)
        arr = slicer.util.arrayFromVolume(labelmap)
        arr_binary = (arr > 0).astype(np.uint8)
        slicer.util.updateVolumeFromArray(labelmap, arr_binary)

        # Import into target as a single segment
        vtk_seg = target_node.GetSegmentation()
        ids_before = {vtk_seg.GetNthSegmentID(i) for i in range(vtk_seg.GetNumberOfSegments())}
        seg_logic.ImportLabelmapToSegmentationNode(labelmap, target_node)
        slicer.mrmlScene.RemoveNode(labelmap)

        # Derive the new segment ID from set difference
        ids_after = {vtk_seg.GetNthSegmentID(i) for i in range(vtk_seg.GetNumberOfSegments())}
        new_ids = ids_after - ids_before
        if not new_ids:
            return  # Source was empty — nothing imported

        # Rename the imported segment to pool_name and set color
        pool_seg_id = new_ids.pop()
        pool_segment = vtk_seg.GetSegment(pool_seg_id)
        pool_segment.SetName(pool_name)
        pool_segment.SetColor(*color)

    def _detect_acquisition_orientation(self, volume_node: Any) -> str:
        """Determine natural acquisition plane from volume's direction matrix.

        Args:
            volume_node: Loaded vtkMRMLScalarVolumeNode.

        Returns:
            "Axial", "Sagittal", or "Coronal".
        """
        import numpy as np  # type: ignore[import-not-found]

        try:
            mat = vtk.vtkMatrix4x4()
            volume_node.GetIJKToRASDirectionMatrix(mat)

            # Third column = slice normal direction
            slice_normal = np.array(
                [
                    mat.GetElement(0, 2),
                    mat.GetElement(1, 2),
                    mat.GetElement(2, 2),
                ]
            )
            dominant = int(np.argmax(np.abs(slice_normal)))
            # 0=L/R → Sagittal, 1=A/P → Coronal, 2=S/I → Axial
            return {0: "Sagittal", 1: "Coronal", 2: "Axial"}[dominant]
        except Exception:
            return "Axial"

    def set_dual_layout(
        self,
        volume_a: Any,
        volume_b: Any,
        seg_a: SegmentationBuilder | Any | None = None,
        seg_b: SegmentationBuilder | Any | None = None,
        linked: bool = True,
        orientation_a: str | None = None,
        orientation_b: str | None = None,
    ) -> None:
        """Set side-by-side layout with two volumes and optional segmentations.

        Args:
            volume_a: Volume node for the left (Red) view.
            volume_b: Volume node for the right (Yellow) view.
            seg_a: Optional segmentation visible only in the left view.
            seg_b: Optional segmentation visible only in the right view.
            linked: If True, link slice navigation between views.
            orientation_a: Orientation for left view ("Axial", "Sagittal",
                "Coronal"). Auto-detected from volume_a if None.
            orientation_b: Orientation for right view ("Axial", "Sagittal",
                "Coronal"). Auto-detected from volume_b if None.
        """
        layout_node = self._layout_manager.layoutLogic().GetLayoutNode()
        layout_node.SetViewArrangement(slicer.vtkMRMLLayoutNode.SlicerLayoutSideBySideView)

        # Configure Red (left) composite
        red_widget = self._layout_manager.sliceWidget("Red")
        red_composite = red_widget.mrmlSliceCompositeNode()
        red_composite.SetBackgroundVolumeID(volume_a.GetID())

        # Configure Yellow (right) composite
        yellow_widget = self._layout_manager.sliceWidget("Yellow")
        yellow_composite = yellow_widget.mrmlSliceCompositeNode()
        yellow_composite.SetBackgroundVolumeID(volume_b.GetID())

        # Restrict segmentation visibility to specific views
        if seg_a is not None:
            node_a = seg_a.node if isinstance(seg_a, SegmentationBuilder) else seg_a
            display_a = node_a.GetDisplayNode()
            if display_a is not None:
                display_a.SetViewNodeIDs(["vtkMRMLSliceNodeRed"])

        if seg_b is not None:
            node_b = seg_b.node if isinstance(seg_b, SegmentationBuilder) else seg_b
            display_b = node_b.GetDisplayNode()
            if display_b is not None:
                display_b.SetViewNodeIDs(["vtkMRMLSliceNodeYellow"])

        # Link slice navigation (SetLinkedControl lives on SliceCompositeNode)
        if linked:
            red_composite = self._scene.GetNodeByID("vtkMRMLSliceCompositeNodeRed")
            yellow_composite = self._scene.GetNodeByID("vtkMRMLSliceCompositeNodeYellow")
            if red_composite is not None:
                red_composite.SetLinkedControl(True)
            if yellow_composite is not None:
                yellow_composite.SetLinkedControl(True)

        self._layout_manager.resetSliceViews()

        # Set per-view orientation (auto-detect from volume if not specified)
        orient_a = orientation_a or self._detect_acquisition_orientation(volume_a)
        orient_b = orientation_b or self._detect_acquisition_orientation(volume_b)

        red_node = self._scene.GetNodeByID("vtkMRMLSliceNodeRed")
        yellow_node = self._scene.GetNodeByID("vtkMRMLSliceNodeYellow")

        if red_node is not None:
            red_node.SetOrientation(orient_a)
        if yellow_node is not None:
            yellow_node.SetOrientation(orient_b)

    def align_by_center(
        self,
        moving_volume: Any,
        reference_volume: Any,
        moving_segmentation: SegmentationBuilder | Any | None = None,
        transform_name: str = "AlignTransform",
    ) -> Any:
        """Align two volumes by translating their image centers.

        Creates a ``vtkMRMLLinearTransformNode`` that shifts *moving_volume*
        so its center coincides with *reference_volume*'s center. Optionally
        applies the same transform to a segmentation.

        Args:
            moving_volume: Volume node to be transformed.
            reference_volume: Target volume node (stays in place).
            moving_segmentation: Optional segmentation to co-transform.
            transform_name: MRML node name for the transform.

        Returns:
            The created ``vtkMRMLLinearTransformNode``.
        """
        ref_bounds = [0.0] * 6
        mov_bounds = [0.0] * 6
        reference_volume.GetRASBounds(ref_bounds)
        moving_volume.GetRASBounds(mov_bounds)

        ref_center = [(ref_bounds[i] + ref_bounds[i + 1]) / 2.0 for i in (0, 2, 4)]
        mov_center = [(mov_bounds[i] + mov_bounds[i + 1]) / 2.0 for i in (0, 2, 4)]

        mat = vtk.vtkMatrix4x4()
        mat.Identity()
        for i in range(3):
            mat.SetElement(i, 3, ref_center[i] - mov_center[i])

        tf_node = slicer.mrmlScene.AddNewNodeByClass(
            "vtkMRMLLinearTransformNode",
            transform_name,
        )
        tf_node.SetMatrixTransformToParent(mat)

        moving_volume.SetAndObserveTransformNodeID(tf_node.GetID())
        if moving_segmentation is not None:
            seg_node = (
                moving_segmentation.node
                if isinstance(moving_segmentation, SegmentationBuilder)
                else moving_segmentation
            )
            seg_node.SetAndObserveTransformNodeID(tf_node.GetID())

        # Reset slice views so they recompute their extent/range to account
        # for the newly transformed volume position.  Without this, scrolling
        # in one view sends the other view outside the transformed data → black.
        self._layout_manager.resetSliceViews()

        return tf_node

    def refine_alignment_by_centroids(
        self,
        moving_seg: SegmentationBuilder | Any,
        reference_seg: SegmentationBuilder | Any,
        transform_node: Any,
        min_landmarks: int = 1,
    ) -> int:
        """Refine alignment using matching segment centroids.

        Collects centroids of segments present in both segmentations,
        computes a rigid-body transform via ``vtkLandmarkTransform``
        (Horn method), and **replaces** the matrix on *transform_node*.

        Moving centroids are in LOCAL RAS (before transform); reference
        centroids are in world RAS (reference has no parent transform).

        Args:
            moving_seg: Moving segmentation (under *transform_node*).
            reference_seg: Reference segmentation (stationary).
            transform_node: Transform node to update in-place.
            min_landmarks: Minimum landmark pairs required to update.

        Returns:
            Number of landmark pairs used (0 means no change applied).
        """
        moving_names = set(self.get_segment_names(moving_seg))
        ref_names = set(self.get_segment_names(reference_seg))
        common = moving_names & ref_names

        source_pts = vtk.vtkPoints()
        target_pts = vtk.vtkPoints()

        for name in sorted(common):
            mov_c = self.get_segment_centroid(moving_seg, name)
            ref_c = self.get_segment_centroid(reference_seg, name)
            if mov_c is None or ref_c is None:
                continue
            source_pts.InsertNextPoint(mov_c)
            target_pts.InsertNextPoint(ref_c)

        n_pairs = source_pts.GetNumberOfPoints()
        if n_pairs < min_landmarks:
            return 0

        landmark_tf = vtk.vtkLandmarkTransform()
        landmark_tf.SetSourceLandmarks(source_pts)
        landmark_tf.SetTargetLandmarks(target_pts)
        landmark_tf.SetModeToRigidBody()
        landmark_tf.Update()

        matrix = vtk.vtkMatrix4x4()
        landmark_tf.GetMatrix(matrix)
        transform_node.SetMatrixTransformToParent(matrix)

        return int(n_pairs)

    def setup_segment_focus_observer(
        self,
        editable_seg: SegmentationBuilder | Any,
        reference_seg: SegmentationBuilder | Any,
        reference_views: list[str] | None = None,
        editable_views: list[str] | None = None,
        only_empty: bool = True,
        on_refine: Any | None = None,
    ) -> None:
        """Auto-navigate to segment centroid when selecting a segment.

        When the user selects a segment in the editor, navigates configured
        views to the centroid of the matching segment. Reference views always
        jump to the reference segmentation centroid. Editable views jump to
        the editable segmentation centroid (or fall back to reference if empty).

        .. note:: **Observer design — lessons learned**

           This callback is attached to ``vtkMRMLSegmentEditorNode`` via
           ``vtkCommand.ModifiedEvent``. Several VTK/Slicer behaviors
           required workarounds:

           1. **Re-entry guard** (``_in_callback``): ``JumpSlice`` can
              trigger further ModifiedEvents on the editor node (e.g. slice
              position changes propagate back). Without the guard the
              callback recurses until Python hits the stack limit or Slicer
              hangs.

           2. **No slicer.app.processEvents()**: Earlier versions used
              ``SegmentStatistics`` which internally calls
              ``processEvents()``. Inside a VTK observer callback this
              causes re-entrant event processing — the callback fires
              again while still executing, leading to deadlocks. All code
              here is pure VTK + numpy with no event processing.

           3. **Per-segment extraction, not SegmentStatistics**: The
              ``SegmentStatistics`` module is convenient but heavyweight
              (processes ALL segments, calls ``processEvents()``). We only
              need one segment's centroid, so direct labelmap extraction
              via ``get_segment_centroid()`` is both faster and
              observer-safe.

        .. note:: **Performance optimizations**

           Each ``get_segment_centroid()`` call does one VTK labelmap
           extraction + one numpy scan. The original code did up to 3
           extractions per click:

           - ``_is_segment_empty()`` → extraction #1 (just to check extent)
           - ``get_segment_centroid(reference)`` → extraction #2
           - ``get_segment_centroid(editable)`` → extraction #3

           Optimizations applied:

           - **Merged emptiness check**: ``get_segment_centroid()`` returns
             ``None`` for empty segments, so a separate ``_is_segment_empty``
             extraction is redundant. We call ``get_segment_centroid()`` on
             the editable node first and use its return value as the
             emptiness signal.
           - **Reference centroid cache** (``_ref_centroids``): The reference
             segmentation (e.g. MasterModel) doesn't change during a
             session. After the first click on a segment, its reference
             centroid is cached. Subsequent clicks skip the VTK extraction
             entirely.

           Result: first click = 2 extractions, subsequent clicks = 1.

        Args:
            editable_seg: The segmentation being edited.
            reference_seg: Reference segmentation with populated segments.
            reference_views: Views to navigate to reference centroid.
                Defaults to ``["Red", "Yellow"]``.
            editable_views: Views to navigate to editable centroid.
                Defaults to ``[]`` (no editable navigation).
            only_empty: If True (default), only navigate when the selected
                segment is empty. If False, navigate for all segments.
            on_refine: Optional callback invoked before centroid computation
                on each segment switch. Use to update alignment transforms
                so that centroids reflect the latest registration.
        """
        if reference_views is None:
            reference_views = ["Red", "Yellow"]
        if editable_views is None:
            editable_views = []

        editable_node = (
            editable_seg.node if isinstance(editable_seg, SegmentationBuilder) else editable_seg
        )
        reference_node = (
            reference_seg.node if isinstance(reference_seg, SegmentationBuilder) else reference_seg
        )

        editor_node = self._scene.GetFirstNodeByClass("vtkMRMLSegmentEditorNode")
        if editor_node is None:
            return

        helper_ref = self  # prevent garbage collection of SlicerHelper

        # Reference segmentation (e.g. MasterModel) is immutable during the
        # session — cache centroids to avoid repeated VTK extraction + numpy.
        _ref_centroids: dict[str, tuple[float, float, float] | None] = {}

        def _jump_views(view_names: list[str], centroid: tuple[float, float, float]) -> None:
            r, a, s = centroid
            for name in view_names:
                node = helper_ref._scene.GetNodeByID(f"vtkMRMLSliceNode{name}")
                if node is not None:
                    node.JumpSlice(r, a, s)

        # Re-entry guard: JumpSlice can trigger ModifiedEvent on the editor
        # node, which would re-invoke this callback. See docstring note #1.
        _in_callback = False

        def on_segment_changed(caller: Any, _event: Any) -> None:
            nonlocal _in_callback
            if _in_callback:
                return
            _in_callback = True
            try:
                seg_id = caller.GetSelectedSegmentID()
                if seg_id is None:
                    print("[SegFocus] no segment selected, skipping")
                    return

                vtk_seg = editable_node.GetSegmentation()
                segment = vtk_seg.GetSegment(seg_id)
                if segment is None:
                    print(f"[SegFocus] segment {seg_id} not found, skipping")
                    return

                segment_name = segment.GetName()

                # Run refinement callback (e.g. update alignment transform)
                # BEFORE centroid computation so coordinates are up-to-date.
                if on_refine is not None:
                    on_refine()

                # Editable centroid doubles as emptiness check: None = empty.
                # Use _local_to_world_centroid to account for parent transforms.
                edit_centroid = helper_ref._local_to_world_centroid(editable_node, segment_name)
                empty = edit_centroid is None
                print(f"[SegFocus] selected: '{segment_name}' (id={seg_id}, empty={empty})")

                if only_empty and not empty:
                    print(f"[SegFocus] skipping non-empty segment (only_empty={only_empty})")
                    return

                # Lookup reference centroid (cached after first computation).
                # Reference has no parent transform, so world = local.
                if segment_name not in _ref_centroids:
                    ref_centroid = helper_ref._local_to_world_centroid(reference_node, segment_name)
                    _ref_centroids[segment_name] = ref_centroid
                    print(f"[SegFocus] ref centroid computed and cached for '{segment_name}'")
                else:
                    ref_centroid = _ref_centroids[segment_name]
                    print(f"[SegFocus] ref centroid from cache for '{segment_name}'")

                if ref_centroid is None:
                    print(f"[SegFocus] no centroid for '{segment_name}' in reference, skipping")
                    return

                print(
                    f"[SegFocus] ref centroid: R={ref_centroid[0]:.1f}, "
                    f"A={ref_centroid[1]:.1f}, S={ref_centroid[2]:.1f}"
                )
                _jump_views(reference_views, ref_centroid)
                print(f"[SegFocus] jumped reference views {reference_views}")

                if editable_views:
                    if empty:
                        _jump_views(editable_views, ref_centroid)
                        print(
                            f"[SegFocus] jumped editable views {editable_views} (using ref, segment empty)"
                        )
                    else:
                        target = edit_centroid or ref_centroid
                        _jump_views(editable_views, target)
                        source = "edit" if edit_centroid else "ref (fallback)"
                        print(
                            f"[SegFocus] jumped editable views {editable_views} "
                            f"(using {source}: R={target[0]:.1f}, A={target[1]:.1f}, S={target[2]:.1f})"
                        )
            except Exception as e:
                print(f"[SlicerHelper] segment focus error: {e}")
            finally:
                _in_callback = False

        tag = editor_node.AddObserver(vtk.vtkCommand.ModifiedEvent, on_segment_changed)
        self._observer_tags.append((editor_node, tag))
