"""Config package for RecordType definitions.

Provides two config modes:
- **TOML mode**: Bidirectional sync between TOML files and DB via API.
- **Python mode**: Python files are the single source of truth; API mutations disabled.

User-facing API::

    from clarinet.config import RecordDef, FileDef, FileRef

    segmentation = FileDef(pattern="seg.nrrd", description="Segmentation mask")

    lesion_seg = RecordDef(
        name="lesion_seg",
        description="Lesion segmentation task",
        files=[FileRef(segmentation, "input")],
    )
"""

from clarinet.config.primitives import FileDef, FileRef, RecordDef

# Backward compatibility aliases
File = FileDef
RecordType = RecordDef

__all__ = ["File", "FileDef", "FileRef", "RecordDef", "RecordType"]
