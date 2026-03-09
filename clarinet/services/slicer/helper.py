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
        """Compute the RAS centroid of a named segment.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            segment_name: Name of the segment to find.

        Returns:
            (R, A, S) centroid tuple, or None if the segment is empty.
        """
        node = segmentation.node if isinstance(segmentation, SegmentationBuilder) else segmentation
        vtk_seg = node.GetSegmentation()

        # Find segment ID by name
        seg_id = None
        for i in range(vtk_seg.GetNumberOfSegments()):
            sid = vtk_seg.GetNthSegmentID(i)
            if vtk_seg.GetSegment(sid).GetName() == segment_name:
                seg_id = sid
                break

        if seg_id is None:
            return None

        import SegmentStatistics  # type: ignore[import-not-found]

        stats_logic = SegmentStatistics.SegmentStatisticsLogic()
        stats_logic.getParameterNode().SetParameter("Segmentation", node.GetID())
        stats_logic.getParameterNode().SetParameter(
            "LabelmapSegmentStatisticsPlugin.centroid_ras.enabled", "True"
        )
        stats_logic.computeStatistics()
        stats = stats_logic.getStatistics()

        centroid_key = f"{seg_id}.LabelmapSegmentStatisticsPlugin.centroid_ras"
        if centroid_key not in stats:
            return None

        centroid = stats[centroid_key]
        return (centroid[0], centroid[1], centroid[2])

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

    def auto_number_segment(
        self,
        segmentation: SegmentationBuilder | Any,
        prefix: str = "ROI",
        start_from: int | None = None,
    ) -> int:
        """Add a new numbered segment with the next available number.

        Parses existing segment names matching ``{prefix}_{N}`` to find the
        highest number, then creates ``{prefix}_{N+1}``.

        Args:
            segmentation: SegmentationBuilder or raw segmentation node.
            prefix: Name prefix for numbered segments.
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
            pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
            for name in names:
                m = pattern.match(name)
                if m:
                    max_num = max(max_num, int(m.group(1)))
            next_num = max_num + 1

        vtk_seg.AddEmptySegment(f"{prefix}_{next_num}", f"{prefix}_{next_num}")
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

        # Export seg_b to a merged labelmap
        seg_logic = slicer.modules.segmentations.logic()
        labelmap_b = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "_sub_b")
        seg_logic.ExportAllSegmentsToLabelmapNode(node_b, labelmap_b)
        arr_b = slicer.util.arrayFromVolume(labelmap_b)

        vtk_seg_a = node_a.GetSegmentation()
        segments_to_remove: list[str] = []

        for i in range(vtk_seg_a.GetNumberOfSegments()):
            seg_id = vtk_seg_a.GetNthSegmentID(i)

            # Export single segment to temporary labelmap
            tmp_label = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLLabelMapVolumeNode", "_sub_tmp")
            seg_logic.ExportSegmentsToLabelmapNode(node_a, [seg_id], tmp_label)
            arr_a = slicer.util.arrayFromVolume(tmp_label)

            mask_a = arr_a > 0
            total = int(np.sum(mask_a))
            if total == 0:
                slicer.mrmlScene.RemoveNode(tmp_label)
                continue

            overlap = int(np.sum(mask_a & (arr_b > 0)))

            remove = overlap > max_overlap
            if max_overlap_ratio is not None:
                remove = remove and (overlap / total > max_overlap_ratio)

            if remove:
                segments_to_remove.append(seg_id)

            slicer.mrmlScene.RemoveNode(tmp_label)

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

    def set_dual_layout(
        self,
        volume_a: Any,
        volume_b: Any,
        seg_a: SegmentationBuilder | Any | None = None,
        seg_b: SegmentationBuilder | Any | None = None,
        linked: bool = True,
    ) -> None:
        """Set side-by-side layout with two volumes and optional segmentations.

        Args:
            volume_a: Volume node for the left (Red) view.
            volume_b: Volume node for the right (Yellow) view.
            seg_a: Optional segmentation visible only in the left view.
            seg_b: Optional segmentation visible only in the right view.
            linked: If True, link slice navigation between views.
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

    def setup_segment_focus_observer(
        self,
        editable_seg: SegmentationBuilder | Any,
        reference_seg: SegmentationBuilder | Any,
    ) -> None:
        """Auto-navigate to reference centroid when selecting an empty segment.

        When the user selects a segment in the editor, if that segment is empty
        in the editable segmentation, the views jump to the centroid of the
        same-named segment in the reference segmentation.

        Args:
            editable_seg: The segmentation being edited.
            reference_seg: Reference segmentation with populated segments.
        """
        editable_node = (
            editable_seg.node if isinstance(editable_seg, SegmentationBuilder) else editable_seg
        )
        reference_node = (
            reference_seg.node if isinstance(reference_seg, SegmentationBuilder) else reference_seg
        )

        editor_node = self._scene.GetFirstNodeByClass("vtkMRMLSegmentEditorNode")
        if editor_node is None:
            return

        helper_ref = self  # capture for closure

        def on_segment_changed(caller: Any, _event: Any) -> None:
            seg_id = caller.GetSelectedSegmentID()
            if seg_id is None:
                return

            vtk_seg = editable_node.GetSegmentation()
            segment = vtk_seg.GetSegment(seg_id)
            if segment is None:
                return

            segment_name = segment.GetName()

            # Check if the segment is empty via binary labelmap
            labelmap = vtk_seg.GetSegmentBinaryLabelmapRepresentation(seg_id)
            if labelmap is not None and labelmap.GetExtent()[0] <= labelmap.GetExtent()[1]:
                # Segment has data — not empty, skip navigation
                return

            centroid = helper_ref.get_segment_centroid(reference_node, segment_name)
            if centroid is None:
                return

            r, a, s = centroid
            for slice_name in ["Red", "Yellow"]:
                slice_node = helper_ref._scene.GetNodeByID(f"vtkMRMLSliceNode{slice_name}")
                if slice_node is not None:
                    slice_node.JumpSlice(r, a, s)

        editor_node.AddObserver(vtk.vtkCommand.ModifiedEvent, on_segment_changed)
