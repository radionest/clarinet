"""Unit tests for the built-in DICOM→NIfTI conversion pipeline task."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from clarinet.exceptions.domain import PipelineStepError
from clarinet.models.base import DicomQueryLevel
from clarinet.services.pipeline.context import FileResolver, RecordQuery, TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.tasks.convert_series import (
    VOLUME_NIFTI,
    _convert_series_impl,
)


def _build_ctx(
    tmp_path: Path,
    series_uid: str = "1.2.3.4",
) -> TaskContext:
    """Build a minimal TaskContext with SERIES-level working dirs."""
    working_dirs = {
        DicomQueryLevel.PATIENT: tmp_path / "patient",
        DicomQueryLevel.STUDY: tmp_path / "patient" / "study",
        DicomQueryLevel.SERIES: tmp_path / "patient" / "study" / series_uid,
    }
    for d in working_dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    files = FileResolver(
        working_dirs=working_dirs,
        record_type_level=DicomQueryLevel.SERIES,
        file_registry=[],
        fields={},
    )
    client = AsyncMock()
    records = RecordQuery(client=client, files=files)
    msg = PipelineMessage(
        patient_id="PAT001",
        study_uid="1.2.3",
        series_uid=series_uid,
    )
    return TaskContext(files=files, records=records, client=client, msg=msg)


class TestConvertSeriesToNifti:
    """Tests for convert_series_to_nifti core logic."""

    @pytest.mark.asyncio
    async def test_missing_series_uid_raises(self, tmp_path: Path):
        """Task raises PipelineStepError when series_uid is missing."""
        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(
            patient_id="PAT001",
            study_uid="1.2.3",
            series_uid=None,
        )
        with pytest.raises(PipelineStepError, match="series_uid is required"):
            await _convert_series_impl(msg, ctx)

    @pytest.mark.asyncio
    async def test_skips_when_nonempty_file_exists(self, tmp_path: Path):
        """Task skips conversion if a non-empty output file already exists."""
        ctx = _build_ctx(tmp_path)
        output_path = ctx.files.resolve(VOLUME_NIFTI)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"existing nifti data")

        msg = PipelineMessage(
            patient_id="PAT001",
            study_uid="1.2.3",
            series_uid="1.2.3.4",
        )

        # Should return early without reaching DICOM imports
        await _convert_series_impl(msg, ctx)

        # File should still be the original (not overwritten)
        assert output_path.read_bytes() == b"existing nifti data"

    @pytest.mark.asyncio
    async def test_reconverts_empty_file(self, tmp_path: Path):
        """Task re-converts if the output file exists but is empty (truncated)."""
        ctx = _build_ctx(tmp_path)
        output_path = ctx.files.resolve(VOLUME_NIFTI)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")  # empty/corrupt file

        msg = PipelineMessage(
            patient_id="PAT001",
            study_uid="1.2.3",
            series_uid="1.2.3.4",
        )

        mock_result = MagicMock()
        mock_result.num_completed = 3
        mock_dicom_client = AsyncMock()
        mock_dicom_client.get_series = AsyncMock(return_value=mock_result)
        mock_img = MagicMock()

        def fake_save(path: Path, filetype: object) -> Path:
            path.write_bytes(b"real nifti content")
            return path

        mock_img.save_as = fake_save

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_dicom_client),
            patch("clarinet.services.dicom.DicomNode"),
            patch("clarinet.services.image.Image", return_value=mock_img),
        ):
            await _convert_series_impl(msg, ctx)

        assert output_path.read_bytes() == b"real nifti content"

    @pytest.mark.asyncio
    async def test_successful_conversion(self, tmp_path: Path):
        """Task downloads DICOM, converts to NIfTI, and saves."""
        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(
            patient_id="PAT001",
            study_uid="1.2.3",
            series_uid="1.2.3.4",
        )

        mock_result = MagicMock()
        mock_result.num_completed = 5

        mock_dicom_client = AsyncMock()
        mock_dicom_client.get_series = AsyncMock(return_value=mock_result)

        mock_img_instance = MagicMock()

        def fake_save_as(path: Path, filetype: object) -> Path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00" * 352 + np.zeros((2, 2, 2), dtype=np.float32).tobytes())
            return path

        mock_img_instance.save_as = fake_save_as

        with (
            patch(
                "clarinet.services.dicom.DicomClient",
                return_value=mock_dicom_client,
            ),
            patch("clarinet.services.dicom.DicomNode"),
            patch(
                "clarinet.services.image.Image",
                return_value=mock_img_instance,
            ),
        ):
            await _convert_series_impl(msg, ctx)

        output_path = ctx.files.resolve(VOLUME_NIFTI)
        assert output_path.is_file()

    @pytest.mark.asyncio
    async def test_zero_instances_raises(self, tmp_path: Path):
        """Task raises PipelineStepError when C-GET returns 0 instances."""
        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(
            patient_id="PAT001",
            study_uid="1.2.3",
            series_uid="1.2.3.4",
        )

        mock_result = MagicMock()
        mock_result.num_completed = 0

        mock_dicom_client = AsyncMock()
        mock_dicom_client.get_series = AsyncMock(return_value=mock_result)

        with (
            patch(
                "clarinet.services.dicom.DicomClient",
                return_value=mock_dicom_client,
            ),
            patch("clarinet.services.dicom.DicomNode"),
            pytest.raises(PipelineStepError, match="0 instances"),
        ):
            await _convert_series_impl(msg, ctx)


class TestVolumeNiftiFileDef:
    """Tests for the VOLUME_NIFTI constant."""

    def test_pattern(self):
        assert VOLUME_NIFTI.pattern == "volume.nii.gz"

    def test_level(self):
        assert VOLUME_NIFTI.level == DicomQueryLevel.SERIES

    def test_resolves_to_series_dir(self, tmp_path: Path):
        """VOLUME_NIFTI resolves to <series_dir>/volume.nii.gz."""
        series_dir = tmp_path / "patient" / "study" / "series"
        series_dir.mkdir(parents=True)
        working_dirs = {
            DicomQueryLevel.PATIENT: tmp_path / "patient",
            DicomQueryLevel.STUDY: tmp_path / "patient" / "study",
            DicomQueryLevel.SERIES: series_dir,
        }
        resolver = FileResolver(
            working_dirs=working_dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[],
            fields={},
        )
        assert resolver.resolve(VOLUME_NIFTI) == series_dir / "volume.nii.gz"
