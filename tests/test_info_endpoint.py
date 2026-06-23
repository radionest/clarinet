import pytest

from clarinet.api.routers.info import get_project_info
from clarinet.services.viewer import ViewerRegistry


@pytest.mark.asyncio
async def test_info_includes_dicomweb_backend():
    info = await get_project_info(registry=ViewerRegistry())
    assert info["dicomweb_backend"] == "builtin"
