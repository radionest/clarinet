"""Clarinet — an imaging-centric framework for clinical research."""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING

from clarinet.client import ClarinetClient as client
from clarinet.config.primitives import FileDef, RecordDef

if TYPE_CHECKING:
    from clarinet.services import dicom as dicom

__all__ = [
    "FileDef",
    "RecordDef",
    "client",
    "dicom",
]


def __getattr__(name: str) -> ModuleType:
    if name == "dicom":
        from clarinet.services import dicom

        return dicom
    raise AttributeError(f"module 'clarinet' has no attribute {name}")
