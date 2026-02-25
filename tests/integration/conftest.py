"""Fixtures for integration tests requiring a running 3D Slicer instance."""

from pathlib import Path

import pytest
import pytest_asyncio

from src.services.slicer.client import SlicerClient
from src.services.slicer.service import SlicerService


@pytest.fixture
def slicer_url() -> str:
    """Base URL for the local Slicer web server."""
    return "http://localhost:2016"


@pytest.fixture
def slicer_service() -> SlicerService:
    """SlicerService instance with cached helper source."""
    return SlicerService()


@pytest_asyncio.fixture
async def slicer_client(slicer_url: str) -> SlicerClient:
    """Async SlicerClient connected to the local Slicer instance."""
    async with SlicerClient(slicer_url) as client:
        yield client


@pytest.fixture
def test_images_path() -> Path:
    """Path to test images directory.

    Place test NRRD/NIfTI files here for Slicer integration tests.
    """
    return Path(__file__).parent / "test_data" / "slicer"
