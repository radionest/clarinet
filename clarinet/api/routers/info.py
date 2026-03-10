"""Public project info endpoint."""

from fastapi import APIRouter

from clarinet.settings import settings

router = APIRouter(tags=["Info"])


@router.get("/info")
async def get_project_info() -> dict:
    """Return project branding information (public, no auth required)."""
    return {
        "project_name": settings.project_name,
        "project_description": settings.project_description,
    }
