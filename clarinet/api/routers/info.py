"""Public project info endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from clarinet.api.dependencies import ViewerRegistryDep
from clarinet.settings import settings

router = APIRouter(tags=["Info"])


@router.get("/info")
async def get_project_info(registry: ViewerRegistryDep) -> dict[str, Any]:
    """Return project branding and viewer configuration (public, no auth required)."""
    return {
        "project_name": settings.project_name,
        "project_description": settings.project_description,
        "viewers": registry.viewer_info(),
    }
