"""HTTP-backed ``AnonymizationService`` factory.

For Record-aware orchestration (skip-guard, Patient anonymization, Record
updates) and the built-in pipeline task, see
``clarinet.services.dicom.orchestrator`` and ``clarinet.services.dicom.pipeline``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from clarinet.services.anonymization_service import AnonymizationService


@asynccontextmanager
async def create_anonymization_service() -> AsyncGenerator[AnonymizationService]:
    """Create an ``AnonymizationService`` backed by HTTP API.

    Workers use ``ClarinetClient`` with repo adapters instead of direct DB
    access, so they only need API credentials (no DB connection).

    Use this when you need raw DICOM anonymization without Record bookkeeping.
    For Record-aware anonymization, use ``create_anonymization_orchestrator``
    from ``clarinet.services.dicom.orchestrator``.
    """
    from clarinet.client import ClarinetClient
    from clarinet.services.dicom.orchestrator import build_anonymization_service
    from clarinet.settings import settings

    async with ClarinetClient(
        base_url=settings.effective_api_base_url,
        service_token=settings.effective_service_token,
        verify_ssl=settings.api_verify_ssl,
    ) as api_client:
        yield build_anonymization_service(api_client)
