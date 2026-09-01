"""The `clarinet worker` DICOM wiring: who gets a Storage SCP, and in which mode.

Both halves are easy to get wrong in ways no other test would catch. Inferring
the listener from the retrieve mode gives a c-move deployment two owners of
`dicom_aet:dicom_port` — the API has held it since before the worker started —
and forcing a bare `c-move` transport silently drops the `-study` suffix the
Slicer helper reads to batch at study level.
"""

from unittest.mock import AsyncMock, patch

import pytest

from clarinet.cli.main import _run_pipeline_worker
from clarinet.settings import settings


async def _wire(
    *, mode: str, scp_enabled: bool | None, dicom_scp: tuple[str, int] | None
) -> tuple[bool, str]:
    """Run the worker entrypoint, returning (start_scp, resulting retrieve mode)."""
    with (
        patch("clarinet.services.pipeline.run_worker", new=AsyncMock()) as run_worker,
        patch.object(settings, "dicom_retrieve_mode", mode),
        patch.object(settings, "dicom_scp_enabled", scp_enabled),
        patch.object(settings, "pipeline_enabled", True),
    ):
        await _run_pipeline_worker(queues=["q"], workers=1, dicom_scp=dicom_scp)
        return run_worker.await_args.kwargs["start_scp"], settings.dicom_retrieve_mode


class TestListenerOwnership:
    """A worker takes a listener only when asked — never from the mode alone."""

    @pytest.mark.parametrize("mode", ["c-move", "c-move-study"])
    async def test_move_mode_alone_does_not_claim_a_listener(self, mode: str):
        """The regression guard: the API already owns the port on such a deployment."""
        start_scp, _ = await _wire(mode=mode, scp_enabled=None, dicom_scp=None)
        assert start_scp is False

    @pytest.mark.parametrize("mode", ["c-get", "c-get-study"])
    async def test_get_mode_does_not_claim_a_listener(self, mode: str):
        start_scp, _ = await _wire(mode=mode, scp_enabled=None, dicom_scp=None)
        assert start_scp is False

    async def test_dicom_flag_claims_a_listener(self):
        start_scp, _ = await _wire(mode="c-get", scp_enabled=None, dicom_scp=("W", 4006))
        assert start_scp is True

    async def test_explicit_enable_claims_a_listener(self):
        start_scp, _ = await _wire(mode="c-move", scp_enabled=True, dicom_scp=None)
        assert start_scp is True

    async def test_dicom_flag_overrides_an_explicit_disable(self):
        """--dicom is the per-process escape from a shared EnvironmentFile."""
        start_scp, _ = await _wire(mode="c-move", scp_enabled=False, dicom_scp=("W", 4006))
        assert start_scp is True


class TestTransportMapping:
    """--dicom switches transport to C-MOVE while preserving the Q/R level."""

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("c-get", "c-move"),
            ("c-get-study", "c-move-study"),
            ("c-move", "c-move"),
            ("c-move-study", "c-move-study"),
        ],
    )
    async def test_level_survives_the_switch(self, configured: str, expected: str):
        _, mode = await _wire(mode=configured, scp_enabled=None, dicom_scp=("W", 4006))
        assert mode == expected

    async def test_mode_untouched_without_the_flag(self):
        _, mode = await _wire(mode="c-get-study", scp_enabled=None, dicom_scp=None)
        assert mode == "c-get-study"
