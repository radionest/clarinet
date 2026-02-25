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

import os
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


class PacsHelper:
    """DSL for PACS query/retrieve inside 3D Slicer via DIMSE.

    Wraps ``ctkDICOMQuery`` + ``ctkDICOMRetrieve`` into concise methods.
    Connection params are injected as context variables by SlicerService.
    """

    def __init__(
        self,
        host: str,
        port: int,
        called_aet: str,
        calling_aet: str,
        prefer_cget: bool = True,
        move_aet: str = "SLICER",
    ) -> None:
        self.host = host
        self.port = port
        self.called_aet = called_aet
        self.calling_aet = calling_aet
        self.prefer_cget = prefer_cget
        self.move_aet = move_aet

    def retrieve_study(self, study_instance_uid: str) -> list[str]:
        """Query PACS by Study UID, retrieve matching series, load into scene.

        Uses ctkDICOMQuery for C-FIND, then ctkDICOMRetrieve for C-GET/C-MOVE,
        and finally DICOMUtils to load the matching series into the MRML scene.

        Args:
            study_instance_uid: DICOM Study Instance UID to retrieve.

        Returns:
            List of loaded MRML node IDs.
        """
        # 1. C-FIND: query PACS for the study
        query = ctk.ctkDICOMQuery()
        query.callingAETitle = self.calling_aet
        query.calledAETitle = self.called_aet
        query.host = self.host
        query.port = self.port
        query.setFilters({"StudyInstanceUID": study_instance_uid})

        temp_db = ctk.ctkDICOMDatabase()
        temp_db.openDatabase("")
        query.query(temp_db)

        # 2. C-GET/C-MOVE: retrieve series into Slicer DICOM database
        retrieve = ctk.ctkDICOMRetrieve()
        retrieve.callingAETitle = self.calling_aet
        retrieve.calledAETitle = self.called_aet
        retrieve.host = self.host
        retrieve.port = self.port
        retrieve.setDatabase(slicer.dicomDatabase)

        if not self.prefer_cget:
            retrieve.setMoveDestinationAETitle(self.move_aet)

        retrieved_series_uids: list[str] = []
        for study_uid, series_uid in query.studyAndSeriesInstanceUIDQueried:
            if study_uid != study_instance_uid:
                continue
            if self.prefer_cget:
                retrieve.getSeries(study_uid, series_uid)
            else:
                retrieve.moveSeries(study_uid, series_uid)
            retrieved_series_uids.append(series_uid)

        # 3. Load ONLY the retrieved series into the MRML scene
        from DICOMLib import DICOMUtils  # type: ignore[import-not-found]

        loaded_node_ids: list[str] = DICOMUtils.loadSeriesByUID(retrieved_series_uids)

        temp_db.closeDatabase()
        return loaded_node_ids or []


class SlicerHelper:
    """DSL for concise 3D Slicer workspace setup.

    Provides a high-level API that wraps verbose Slicer Python calls into
    short, chainable methods. Designed to run inside the 3D Slicer environment.
    """

    def __init__(self, working_folder: str) -> None:
        """Clear scene and set root directory.

        Args:
            working_folder: Absolute path to the working directory.
        """
        self.working_folder = working_folder
        self._scene = slicer.mrmlScene
        self._layout_manager = slicer.app.layoutManager()
        self._image_node: Any = None
        self._editor_widget: Any = None

        # Clear scene and set working folder
        self._scene.Clear(0)
        self._scene.SetRootDirectory(working_folder)

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

    def setup_editor(
        self,
        segmentation: SegmentationBuilder | Any,
        effect: EditorEffectName = "Paint",
        brush_size: float = 20.0,
        threshold: tuple[float, float] | None = None,
        sphere_brush: bool = True,
    ) -> None:
        """Open SegmentEditor and configure tools.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            effect: Effect name to activate.
            brush_size: Brush diameter in mm.
            threshold: Optional (min, max) threshold values for Threshold effect.
            sphere_brush: Use spherical brush (True) or circular (False).
        """
        seg_node = (
            segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        )

        slicer.util.selectModule("SegmentEditor")
        self._editor_widget = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        self._editor_widget.setSegmentationNode(seg_node)

        if self._image_node is not None:
            self._editor_widget.setSourceVolumeNode(self._image_node)

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

    def load_study_from_pacs(self, study_instance_uid: str) -> list[str]:
        """Load a DICOM study from PACS into the current scene.

        Requires ``pacs_*`` context variables to be injected by SlicerService.

        Args:
            study_instance_uid: DICOM Study Instance UID to retrieve.

        Returns:
            List of loaded MRML node IDs.
        """
        pacs = PacsHelper(
            host=pacs_host,  # type: ignore[name-defined]  # noqa: F821
            port=pacs_port,  # type: ignore[name-defined]  # noqa: F821
            called_aet=pacs_aet,  # type: ignore[name-defined]  # noqa: F821
            calling_aet=pacs_calling_aet,  # type: ignore[name-defined]  # noqa: F821
            prefer_cget=pacs_prefer_cget,  # type: ignore[name-defined]  # noqa: F821
            move_aet=pacs_move_aet,  # type: ignore[name-defined]  # noqa: F821
        )
        return pacs.retrieve_study(study_instance_uid)
