"""Clarinet frontend module.

This module contains the Gleam/Lustre-based frontend for the Clarinet medical imaging framework.
The frontend is built as a single-page application (SPA) that communicates with the FastAPI backend.
"""

from pathlib import Path

FRONTEND_DIR = Path(__file__).parent
BUILD_DIR = FRONTEND_DIR / "build"
STATIC_DIR = FRONTEND_DIR / "static"
SRC_DIR = FRONTEND_DIR / "src"

__all__ = ["BUILD_DIR", "FRONTEND_DIR", "SRC_DIR", "STATIC_DIR"]
