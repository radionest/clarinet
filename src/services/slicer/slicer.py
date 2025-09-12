import os
from typing import TYPE_CHECKING, Any, Literal

# slicer module is only available when running inside 3D Slicer environment
if TYPE_CHECKING:
    # For type checking, treat slicer as Any
    slicer: Any
else:
    try:
        import slicer  # type: ignore[import-not-found]
    except ImportError:
        # Create a dummy slicer module for testing outside Slicer
        class DummySlicer:
            """Dummy slicer module for running outside 3D Slicer environment."""

            mrmlScene = None
            app = None
            util = None

        slicer = DummySlicer()

EditorEffectsNames = Literal["Paint", "Erase"]


class Slicer:
    def __init__(self, working_folder: str) -> None:
        self.working_folder = working_folder
        self.main_img: Any = ...  # Placeholder for main image node
        self.segmentations: dict[str, Any] = {}
        # Initialize slicer-related attributes
        self.slicer = slicer
        self.scene = slicer.mrmlScene if hasattr(slicer, "mrmlScene") else None
        self.layout = slicer.app.layoutManager() if hasattr(slicer, "app") and slicer.app else None

    def load_segmentation(self, path: str, name: str | None = None) -> None:
        name = name if name else path.split(".")[0]
        segmentation_node = self.slicer.util.loadSegmentation(
            os.path.join(self.working_folder, path)
        )
        segmentation_node.SetReferenceImageGeometryParameterFromVolumeNode(self.main_img)
        segmentation_node.CreateDefaultDisplayNodes()
        self.segmentations[name] = segmentation_node
