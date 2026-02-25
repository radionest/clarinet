"""Integration tests for SlicerClient — require a running 3D Slicer instance."""

import pytest

from src.exceptions import SlicerError
from src.services.slicer.client import SlicerClient

pytestmark = [pytest.mark.slicer, pytest.mark.asyncio]


async def test_ping(slicer_client: SlicerClient) -> None:
    """Slicer should respond to a trivial script."""
    assert await slicer_client.ping() is True


async def test_execute_simple_script(slicer_client: SlicerClient) -> None:
    """Execute print('hello') and verify we get a valid response."""
    result = await slicer_client.execute("print('hello')")
    assert isinstance(result, dict)


async def test_execute_invalid_script(slicer_client: SlicerClient) -> None:
    """An invalid script should raise SlicerError."""
    with pytest.raises(SlicerError):
        await slicer_client.execute("raise RuntimeError('test error')")


async def test_ping_unreachable() -> None:
    """Ping to an unreachable host should return False."""
    async with SlicerClient("http://192.0.2.1:2016", timeout=1.0) as client:
        assert await client.ping() is False
