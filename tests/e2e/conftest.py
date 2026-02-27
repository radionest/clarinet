"""E2E test configuration — uses unauthenticated client for auth workflow tests."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def client(unauthenticated_client) -> AsyncGenerator[AsyncClient]:
    """Override client with unauthenticated version for e2e auth tests."""
    yield unauthenticated_client
