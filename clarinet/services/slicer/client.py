"""Async HTTP client for 3D Slicer web server."""

import time
import uuid
from typing import Any, cast

import httpx

from clarinet.exceptions import SlicerConnectionError, SlicerError
from clarinet.utils.logger import logger


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
            Dict from Slicer's ``__execResult`` variable. The script must assign
            a dict to ``__execResult`` for it to appear in the response.
            ``print()`` output goes to Slicer console only, not the HTTP response.

        Raises:
            SlicerConnectionError: If the connection fails or times out.
            SlicerError: If Slicer returns a non-200 status.
        """
        # Observability: correlate request across client, Slicer console,
        # and test logs. Measure wall-clock to diagnose flaky timeouts.
        req_id = uuid.uuid4().hex[:8]
        script_size = len(script)
        logger.info(f"slicer.exec start req_id={req_id} url={self.url} size={script_size}B")
        t0 = time.perf_counter()
        try:
            response = await self._client.post(
                f"{self.url}/slicer/exec",
                content=script,
                headers={"X-Request-Id": req_id},
            )
        except httpx.ConnectError as e:
            elapsed = time.perf_counter() - t0
            logger.error(
                f"slicer.exec connect_error req_id={req_id} elapsed={elapsed:.2f}s url={self.url}"
            )
            raise SlicerConnectionError(f"Cannot connect to Slicer at {self.url}") from e
        except httpx.TimeoutException as e:
            elapsed = time.perf_counter() - t0
            logger.error(
                f"slicer.exec timeout req_id={req_id} elapsed={elapsed:.2f}s "
                f"size={script_size}B timeout={self._client.timeout}"
            )
            raise SlicerConnectionError(f"Connection to Slicer at {self.url} timed out") from e

        elapsed = time.perf_counter() - t0
        if response.status_code != 200:
            logger.error(
                f"slicer.exec http_error req_id={req_id} elapsed={elapsed:.2f}s "
                f"status={response.status_code} body={response.text[:200]!r}"
            )
            raise SlicerError(f"Slicer execution failed: {response.text}")

        logger.info(
            f"slicer.exec done req_id={req_id} elapsed={elapsed:.2f}s "
            f"status=200 resp_size={len(response.content)}B"
        )
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

    async def __aenter__(self) -> "SlicerClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()
