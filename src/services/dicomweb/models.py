"""Models for DICOMweb proxy service."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class CachedSeries(BaseModel):
    """Metadata for a cached DICOM series on disk."""

    study_uid: str
    series_uid: str
    cache_dir: Path
    instance_paths: list[Path] = Field(default_factory=list)
    cached_at: float  # time.time() when cached


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
