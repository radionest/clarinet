"""
Viewer plugin system for external DICOM viewers.

Provides a registry of viewer adapters that generate URIs for
external viewers (RadiAnt, Weasis, etc.) supporting URI schemes.
"""

from clarinet.services.viewer.adapters import (
    OHIFAdapter,
    RadiantAdapter,
    TemplateAdapter,
    WeasisAdapter,
)
from clarinet.services.viewer.base import ViewerAdapter
from clarinet.services.viewer.registry import ViewerRegistry, build_viewer_registry

__all__ = [
    "OHIFAdapter",
    "RadiantAdapter",
    "TemplateAdapter",
    "ViewerAdapter",
    "ViewerRegistry",
    "WeasisAdapter",
    "build_viewer_registry",
]
