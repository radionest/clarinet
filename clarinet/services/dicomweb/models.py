"""Models for DICOMweb proxy service."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MemoryCachedSeries:
    """In-memory cached DICOM series with O(1) instance lookup.

    Not a Pydantic model because pydicom Dataset is not serializable.
    """

    study_uid: str
    series_uid: str
    instances: dict[str, Any] = field(repr=False)
    cached_at: float
    disk_persisted: bool = False
