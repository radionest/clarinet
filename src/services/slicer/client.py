"""Async HTTP client for 3D Slicer web server."""

from typing import Any, cast

import httpx

from src.exceptions import SlicerConnectionError, SlicerError
from src.utils.logger import logger


class SlicerClient:
    """Async HTTP client for communicating with 3D Slicer's web server.

    Sends Python scripts to ``POST /slicer/exec`` and returns the JSON response.

    Args:
        url: Base URL of the Slicer web server (e.g. ``http://192.168.1.5:2016``).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        self.url = url
        self._client = httpx.AsyncClient(timeout=timeout)

    async def execute(self, script: str) -> dict[str, Any]:
        """POST a Python script to Slicer for execution.

        Args:
            script: Python code to execute inside Slicer.

        Returns:
            JSON response from Slicer.

        Raises:
            SlicerConnectionError: If the connection fails or times out.
            SlicerError: If Slicer returns a non-200 status.
        """
        try:
            response = await self._client.post(f"{self.url}/slicer/exec", content=script)
        except httpx.ConnectError as e:
            raise SlicerConnectionError(f"Cannot connect to Slicer at {self.url}") from e
        except httpx.TimeoutException as e:
            raise SlicerConnectionError(f"Connection to Slicer at {self.url} timed out") from e

        if response.status_code != 200:
            logger.error(f"Slicer error: {response.status_code} - {response.text}")
            raise SlicerError(f"Slicer execution failed: {response.text}")

        return cast("dict[str, Any]", response.json())

    async def ping(self) -> bool:
        """Test connection with a trivial script.

        Returns:
            True if Slicer responds successfully.
        """
        try:
            await self.execute("print('pong')")
        except (SlicerConnectionError, SlicerError):
            return False
        return True

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> SlicerClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
