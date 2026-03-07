"""Config package for RecordType definitions.

Provides two config modes:
- **TOML mode**: Bidirectional sync between TOML files and DB via API.
- **Python mode**: Python files are the single source of truth; API mutations disabled.

User-facing API::

    from clarinet.config import RecordType, File, FileRef

    segmentation = File(pattern="seg.nrrd", description="Segmentation mask")

    lesion_seg = RecordType(
        name="lesion_seg",
        description="Lesion segmentation task",
        files=[FileRef(segmentation, role=FileRole.INPUT)],
    )
"""

from clarinet.config.primitives import File, FileRef
from clarinet.config.primitives import RecordTypeDef as RecordType

__all__ = ["File", "FileRef", "RecordType"]
