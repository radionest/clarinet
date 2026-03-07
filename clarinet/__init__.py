"""Clarinet — A Framework for Clinical-Radiological Studies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from clarinet.client import ClarinetClient as client
from clarinet.config.primitives import File as FileDef
from clarinet.config.primitives import RecordTypeDef

if TYPE_CHECKING:
    from clarinet.services import dicom as dicom

__all__ = [
    "FileDef",
    "RecordTypeDef",
    "client",
    "dicom",
]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    if name == "dicom":
        from clarinet.services import dicom

        return dicom
    raise AttributeError(f"module 'clarinet' has no attribute {name}")
