"""
Slicer router for the Clarinet framework.

This module provides API endpoints for interacting with 3D Slicer instances,
including running scripts and validating segmentations.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.exceptions import NoScriptError, ScriptArgumentError, SlicerConnectionError
from src.types import SlicerResult
from src.utils.logger import logger
from src.utils.slicer import SlicerWeb

router = APIRouter(tags=["Slicer"])


class SlicerScript(BaseModel):
    """Model for Slicer script execution request."""

    script_name: str
    working_folder: str
    slicer_script_args: dict[str, str]


def get_client_ip(request: Request) -> str:
    """Get the client's IP address from the request.

    Args:
        request: The FastAPI request

    Returns:
        The client's IP address as a string

    Raises:
        HTTPException: If client information is not available
    """
    if request.client is None:
        raise HTTPException(status_code=400, detail="Client information not available")
    return request.client.host


def get_webslicer(client_ip: str = Depends(get_client_ip)) -> SlicerWeb:
    """Get a SlicerWeb instance connected to the client's Slicer.

    Args:
        client_ip: The client's IP address

    Returns:
        A SlicerWeb instance connected to the client's Slicer
    """
    slicer = SlicerWeb(f"http://{client_ip}:2016")
    return slicer


@router.post("/run")
async def run_script(
    slicer_script: SlicerScript, slicer: SlicerWeb = Depends(get_webslicer)
) -> SlicerResult:
    """Run a script on the client's Slicer instance.

    Args:
        slicer_script: The script to run and its arguments
        slicer: A SlicerWeb instance connected to the client's Slicer

    Returns:
        The response from Slicer

    Raises:
        NoScriptError: If the script cannot be found
        ScriptArgumentError: If the script arguments are invalid
        SlicerConnectionError: If connection to Slicer fails
    """
    try:
        slicer_response = slicer.run_script(
            slicer_script.script_name,
            working_folder=slicer_script.working_folder,
            **slicer_script.slicer_script_args,
        )
        return slicer_response
    except NoScriptError as e:
        logger.error(f"Script not found: {e}")
        raise
    except ScriptArgumentError as e:
        logger.error(f"Invalid script arguments: {e}")
        raise
    except SlicerConnectionError:
        logger.error(f"Could not connect to Slicer at {slicer.url}")
        raise
