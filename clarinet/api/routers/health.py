"""Health check endpoint for deployment verification."""

from fastapi import APIRouter

from clarinet.settings import settings
from clarinet.utils.db_manager import db_manager
from clarinet.utils.logger import logger

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check() -> dict:
    """Return system health status (public, no auth required).

    Checks database connectivity and pipeline broker availability.
    """
    db_status = await _check_database()
    pipeline_status = _check_pipeline()

    statuses = [db_status, pipeline_status]
    if all(s == "ok" for s in statuses):
        overall = "ok"
    elif "error" in statuses:
        overall = "unhealthy"
    else:
        overall = "degraded"

    return {
        "status": overall,
        "database": db_status,
        "pipeline": pipeline_status,
        "version": "0.0a.1",
    }


async def _check_database() -> str:
    """Check database connectivity with SELECT 1."""
    try:
        async with db_manager.get_async_session_context() as session:
            from sqlalchemy import text

            await session.execute(text("SELECT 1"))
        return "ok"
    except Exception as e:
        logger.error(f"Health check: database error: {e}")
        return "error"


def _check_pipeline() -> str:
    """Check pipeline broker status."""
    if not settings.pipeline_enabled:
        return "disabled"
    try:
        from clarinet.services.pipeline import get_broker

        broker = get_broker()
        if broker is not None:
            return "ok"
        return "error"
    except Exception as e:
        logger.error(f"Health check: pipeline error: {e}")
        return "error"
