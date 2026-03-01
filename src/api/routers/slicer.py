"""Slicer router for the Clarinet framework.

Provides API endpoints for executing scripts on 3D Slicer instances
via the SlicerService DSL layer.
"""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import (
    CurrentUserDep,
    RecordRepositoryDep,
    SlicerServiceDep,
    get_client_ip,
)
from src.exceptions.domain import NoScriptError
from src.models import RecordRead
from src.settings import settings
from src.utils.logger import logger

router = APIRouter(tags=["Slicer"])


class SlicerExecRequest(BaseModel):
    """Request model for script execution."""

    script: str
    context: dict[str, Any] | None = None


class SlicerExecResponse(BaseModel):
    """Response model for script execution."""

    result: dict[str, Any]


@router.post("/exec")
async def execute_script(
    request: SlicerExecRequest,
    service: SlicerServiceDep,
    _current_user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
) -> dict[str, Any]:
    """Execute a script on the user's local Slicer instance.

    The helper DSL is automatically prepended to the script.

    Args:
        request: Script and optional context variables.
        service: Injected SlicerService.
        current_user: Authenticated user.
        client_ip: Client IP for Slicer URL construction.

    Returns:
        JSON response from Slicer.
    """
    slicer_url = f"http://{client_ip}:{settings.slicer_port}"
    return await service.execute(slicer_url, request.script, request.context)


@router.post("/exec/raw")
async def execute_raw_script(
    request: SlicerExecRequest,
    service: SlicerServiceDep,
    _current_user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
) -> dict[str, Any]:
    """Execute a raw script without the helper DSL prepended.

    Args:
        request: Script to execute (context is ignored).
        service: Injected SlicerService.
        current_user: Authenticated user.
        client_ip: Client IP for Slicer URL construction.

    Returns:
        JSON response from Slicer.
    """
    slicer_url = f"http://{client_ip}:{settings.slicer_port}"
    return await service.execute_raw(slicer_url, request.script)


@router.get("/ping")
async def ping_slicer(
    service: SlicerServiceDep,
    _current_user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
) -> dict[str, bool]:
    """Check if the user's local Slicer instance is reachable.

    Args:
        service: Injected SlicerService.
        current_user: Authenticated user.
        client_ip: Client IP for Slicer URL construction.

    Returns:
        {"ok": true/false}
    """
    slicer_url = f"http://{client_ip}:{settings.slicer_port}"
    ok = await service.ping(slicer_url)
    return {"ok": ok}


def _build_pacs_context() -> dict[str, Any]:
    """Build PACS settings context dict for Slicer scripts."""
    return {
        "pacs_host": settings.pacs_host,
        "pacs_port": settings.pacs_port,
        "pacs_aet": settings.pacs_aet,
        "pacs_calling_aet": settings.pacs_calling_aet,
        "pacs_prefer_cget": settings.pacs_prefer_cget,
        "pacs_move_aet": settings.pacs_move_aet,
    }


@router.post("/clear")
async def clear_slicer_scene(
    service: SlicerServiceDep,
    _current_user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
) -> dict[str, bool]:
    """Clear the current scene in the user's local 3D Slicer.

    Args:
        service: Injected SlicerService.
        _current_user: Authenticated user.
        client_ip: Client IP for Slicer URL construction.

    Returns:
        {"ok": true} on success.
    """
    slicer_url = f"http://{client_ip}:{settings.slicer_port}"
    await service.execute_raw(slicer_url, "slicer.mrmlScene.Clear(0)")
    return {"ok": True}


@router.post("/records/{record_id}/open")
async def open_record_in_slicer(
    record_id: int,
    record_repo: RecordRepositoryDep,
    service: SlicerServiceDep,
    _current_user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
) -> dict[str, Any]:
    """Open a record's workspace in the user's local 3D Slicer.

    Loads the record with relations, takes its record_type's slicer_script
    and the record's formatted args, then sends the script to Slicer.

    Args:
        record_id: Record ID to open.
        record_repo: Injected RecordRepository.
        service: Injected SlicerService.
        _current_user: Authenticated user.
        client_ip: Client IP for Slicer URL construction.

    Returns:
        JSON response from Slicer.

    Raises:
        NoScriptError: If the record type has no slicer_script configured.
    """
    record = await record_repo.get_with_relations(record_id)
    record_read = RecordRead.model_validate(record)

    if not record_read.record_type.slicer_script:
        raise NoScriptError(f"Record type has no slicer_script configured for record {record_id}")

    args: dict[str, str] | None = record_read.slicer_all_args_formatted  # type: ignore[assignment]
    context: dict[str, Any] = dict(args or {})
    context.update(_build_pacs_context())

    slicer_url = f"http://{client_ip}:{settings.slicer_port}"
    logger.info(f"Opening record {record_id} in Slicer at {slicer_url}")
    return await service.execute(
        slicer_url, record_read.record_type.slicer_script, context, request_timeout=60.0
    )


@router.post("/records/{record_id}/validate")
async def validate_record_in_slicer(
    record_id: int,
    record_repo: RecordRepositoryDep,
    service: SlicerServiceDep,
    _current_user: CurrentUserDep,
    client_ip: str = Depends(get_client_ip),
) -> dict[str, Any]:
    """Run the result validation script for a record in 3D Slicer.

    Loads the record with relations, takes its record_type's slicer_result_validator
    and the record's formatted args, then sends the script to Slicer.

    Args:
        record_id: Record ID to validate.
        record_repo: Injected RecordRepository.
        service: Injected SlicerService.
        _current_user: Authenticated user.
        client_ip: Client IP for Slicer URL construction.

    Returns:
        JSON response from Slicer.

    Raises:
        NoScriptError: If the record type has no slicer_result_validator configured.
    """
    record = await record_repo.get_with_relations(record_id)
    record_read = RecordRead.model_validate(record)

    if not record_read.record_type.slicer_result_validator:
        raise NoScriptError(
            f"Record type has no slicer_result_validator configured for record {record_id}"
        )

    args: dict[str, str] | None = record_read.slicer_all_args_formatted  # type: ignore[assignment]
    context: dict[str, Any] = dict(args or {})
    context.update(_build_pacs_context())

    slicer_url = f"http://{client_ip}:{settings.slicer_port}"
    logger.info(f"Validating record {record_id} in Slicer at {slicer_url}")
    return await service.execute(
        slicer_url, record_read.record_type.slicer_result_validator, context, request_timeout=60.0
    )
