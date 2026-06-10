"""Integration tests for SlicerService — require a running 3D Slicer instance.

Pure ``_build_script`` unit tests live in ``tests/test_slicer_build_script.py``:
this module is gated behind the ``_check_slicer`` session fixture, so anything
placed here silently skips in CI.
"""

import pytest

from clarinet.services.slicer.service import SlicerService

pytestmark = [
    pytest.mark.slicer,
    pytest.mark.asyncio,
    pytest.mark.usefixtures("_check_slicer"),
    pytest.mark.xdist_group("slicer"),
]


async def test_execute_with_helper(slicer_service: SlicerService, slicer_url: str) -> None:
    """Script with prepended helper should have SlicerHelper available."""
    script = "print(type(SlicerHelper).__name__)"
    result = await slicer_service.execute(slicer_url, script)
    assert isinstance(result, dict)


async def test_execute_with_context(slicer_service: SlicerService, slicer_url: str) -> None:
    """Context variables should be accessible in the script."""
    script = "print(my_var)"
    context = {"my_var": "hello_from_context"}
    result = await slicer_service.execute(slicer_url, script, context=context)
    assert isinstance(result, dict)


async def test_execute_raw(slicer_service: SlicerService, slicer_url: str) -> None:
    """Raw execution should not prepend helper code."""
    result = await slicer_service.execute_raw(slicer_url, "print('raw')")
    assert isinstance(result, dict)


async def test_ping(slicer_service: SlicerService, slicer_url: str) -> None:
    """Ping should succeed against a running Slicer."""
    assert await slicer_service.ping(slicer_url) is True
