"""Unit tests for Pipeline TaskContext system.

Tests FileResolver, RecordQuery, TaskContext, build_task_context, and pipeline_task
decorator — no RabbitMQ, no DB.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exceptions.domain import PipelineStepError
from src.models.base import DicomQueryLevel
from src.models.file_schema import FileDefinitionRead, FileRole, RecordFileLinkRead
from src.services.pipeline.context import (
    FileResolver,
    RecordQuery,
    TaskContext,
    _resolve_pattern_from_dict,
    build_task_context,
)
from src.services.pipeline.message import PipelineMessage

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_file_def(
    name: str = "segmentation",
    pattern: str = "seg.nrrd",
    *,
    role: FileRole = FileRole.OUTPUT,
    required: bool = True,
    multiple: bool = False,
    level: DicomQueryLevel | None = None,
) -> FileDefinitionRead:
    return FileDefinitionRead(
        name=name,
        pattern=pattern,
        role=role,
        required=required,
        multiple=multiple,
        level=level,
    )


def _make_record_type_read(
    name: str = "ct_segmentation",
    level: DicomQueryLevel = DicomQueryLevel.SERIES,
    file_registry: list[FileDefinitionRead] | None = None,
) -> MagicMock:
    rt = MagicMock()
    rt.name = name
    rt.level = level
    rt.file_registry = file_registry or []
    return rt


def _make_patient(
    patient_id: str = "PAT001",
    anon_id: str | None = "CLARINET_1",
    anon_name: str | None = "Anon Patient",
) -> MagicMock:
    p = MagicMock()
    p.id = patient_id
    p.anon_id = anon_id
    p.anon_name = anon_name
    return p


def _make_study(
    study_uid: str = "1.2.3.4.5",
    anon_uid: str | None = "9.8.7.6.5",
    patient_id: str = "PAT001",
) -> MagicMock:
    s = MagicMock()
    s.study_uid = study_uid
    s.anon_uid = anon_uid
    s.patient_id = patient_id
    return s


def _make_series(
    series_uid: str = "1.2.3.4.5.6",
    anon_uid: str | None = "9.8.7.6.5.4",
    study_uid: str = "1.2.3.4.5",
) -> MagicMock:
    s = MagicMock()
    s.series_uid = series_uid
    s.anon_uid = anon_uid
    s.study_uid = study_uid
    return s


def _make_record_read(
    record_id: int = 42,
    patient_id: str = "PAT001",
    study_uid: str = "1.2.3.4.5",
    series_uid: str = "1.2.3.4.5.6",
    level: DicomQueryLevel = DicomQueryLevel.SERIES,
    file_registry: list[FileDefinitionRead] | None = None,
    file_links: list[RecordFileLinkRead] | None = None,
    data: dict | None = None,
) -> MagicMock:
    record = MagicMock()
    record.id = record_id
    record.patient_id = patient_id
    record.study_uid = study_uid
    record.series_uid = series_uid
    record.user_id = None
    record.data = data or {}
    record.clarinet_storage_path = None
    record.file_links = file_links

    record.patient = _make_patient(patient_id)
    record.study = _make_study(study_uid)
    record.series = _make_series(series_uid)
    record.record_type = _make_record_type_read(level=level, file_registry=file_registry or [])
    return record


# ── _resolve_pattern_from_dict ─────────────────────────────────────────────


class TestResolvePatternFromDict:
    """Tests for _resolve_pattern_from_dict."""

    def test_simple_replacement(self):
        result = _resolve_pattern_from_dict("result_{id}.json", {"id": 42})
        assert result == "result_42.json"

    def test_dotted_path(self):
        result = _resolve_pattern_from_dict(
            "birads_{data.BIRADS_R}.txt",
            {"data": {"BIRADS_R": 4}},
        )
        assert result == "birads_4.txt"

    def test_missing_key_returns_empty(self):
        result = _resolve_pattern_from_dict("file_{missing}.txt", {"id": 1})
        assert result == "file_.txt"

    def test_no_placeholders(self):
        result = _resolve_pattern_from_dict("static_name.nrrd", {"id": 1})
        assert result == "static_name.nrrd"


# ── FileResolver.build_working_dirs ─────────────────────────────────────────


class TestBuildWorkingDirs:
    """Tests for FileResolver.build_working_dirs."""

    @patch("src.services.pipeline.context.settings")
    def test_series_level_record(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        record = _make_record_read(level=DicomQueryLevel.SERIES)

        dirs = FileResolver.build_working_dirs(record)

        assert DicomQueryLevel.PATIENT in dirs
        assert DicomQueryLevel.STUDY in dirs
        assert DicomQueryLevel.SERIES in dirs
        assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")
        assert dirs[DicomQueryLevel.STUDY] == Path("/data/CLARINET_1/9.8.7.6.5")
        assert dirs[DicomQueryLevel.SERIES] == Path("/data/CLARINET_1/9.8.7.6.5/9.8.7.6.5.4")

    @patch("src.services.pipeline.context.settings")
    def test_study_level_record(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        record = _make_record_read(level=DicomQueryLevel.STUDY)
        record.series = None
        record.series_uid = None

        dirs = FileResolver.build_working_dirs(record)

        assert DicomQueryLevel.PATIENT in dirs
        assert DicomQueryLevel.STUDY in dirs
        assert DicomQueryLevel.SERIES not in dirs

    @patch("src.services.pipeline.context.settings")
    def test_patient_level_record(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        record = _make_record_read(level=DicomQueryLevel.PATIENT)
        record.series = None
        record.study = None
        record.study_uid = None
        record.series_uid = None

        dirs = FileResolver.build_working_dirs(record)

        assert DicomQueryLevel.PATIENT in dirs
        assert DicomQueryLevel.STUDY not in dirs
        assert DicomQueryLevel.SERIES not in dirs
        assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")

    @patch("src.services.pipeline.context.settings")
    def test_fallback_to_patient_id_when_no_anon(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        record = _make_record_read()
        record.patient.anon_id = None

        dirs = FileResolver.build_working_dirs(record)

        assert dirs[DicomQueryLevel.PATIENT] == Path("/data/PAT001")

    @patch("src.services.pipeline.context.settings")
    def test_custom_storage_path(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/default"
        record = _make_record_read()
        record.clarinet_storage_path = "/custom"

        dirs = FileResolver.build_working_dirs(record)

        assert str(dirs[DicomQueryLevel.PATIENT]).startswith("/custom")


# ── FileResolver.build_fields ───────────────────────────────────────────────


class TestBuildFields:
    """Tests for FileResolver.build_fields."""

    def test_extracts_basic_fields(self):
        record = _make_record_read(record_id=42, patient_id="P001")
        fields = FileResolver.build_fields(record)

        assert fields["id"] == 42
        assert fields["patient_id"] == "P001"
        assert fields["study_uid"] == "1.2.3.4.5"
        assert fields["series_uid"] == "1.2.3.4.5.6"
        assert fields["user_id"] is None

    def test_flattens_data(self):
        record = _make_record_read(data={"BIRADS_R": 4, "side": "left"})
        fields = FileResolver.build_fields(record)

        assert fields["data"]["BIRADS_R"] == 4
        assert fields["data"]["side"] == "left"

    def test_includes_record_type_name(self):
        record = _make_record_read()
        fields = FileResolver.build_fields(record)

        assert fields["record_type"]["name"] == "ct_segmentation"


# ── FileResolver.dir ────────────────────────────────────────────────────────


class TestFileResolverDir:
    """Tests for FileResolver.dir()."""

    def test_default_level(self):
        dirs = {
            DicomQueryLevel.PATIENT: Path("/data/P1"),
            DicomQueryLevel.STUDY: Path("/data/P1/S1"),
            DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1"),
        }
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[],
            fields={},
        )
        assert resolver.dir() == Path("/data/P1/S1/SE1")

    def test_explicit_level(self):
        dirs = {
            DicomQueryLevel.PATIENT: Path("/data/P1"),
            DicomQueryLevel.STUDY: Path("/data/P1/S1"),
            DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1"),
        }
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[],
            fields={},
        )
        assert resolver.dir(DicomQueryLevel.PATIENT) == Path("/data/P1")

    def test_missing_level_raises(self):
        dirs = {DicomQueryLevel.PATIENT: Path("/data/P1")}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.PATIENT,
            file_registry=[],
            fields={},
        )
        with pytest.raises(KeyError):
            resolver.dir(DicomQueryLevel.SERIES)


# ── FileResolver.resolve ────────────────────────────────────────────────────


class TestFileResolverResolve:
    """Tests for FileResolver.resolve()."""

    def test_simple_pattern(self):
        fd = _make_file_def(pattern="seg.nrrd")
        dirs = {DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1")}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={},
        )
        assert resolver.resolve(fd) == Path("/data/P1/S1/SE1/seg.nrrd")

    def test_pattern_with_placeholders(self):
        fd = _make_file_def(pattern="result_{id}.json")
        dirs = {DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1")}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={"id": 42},
        )
        assert resolver.resolve(fd) == Path("/data/P1/S1/SE1/result_42.json")

    def test_overrides(self):
        fd = _make_file_def(pattern="result_{id}.json")
        dirs = {DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1")}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={"id": 42},
        )
        assert resolver.resolve(fd, id=99) == Path("/data/P1/S1/SE1/result_99.json")

    def test_resolve_by_name(self):
        fd = _make_file_def(name="segmentation", pattern="seg.nrrd")
        dirs = {DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1")}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={},
        )
        assert resolver.resolve("segmentation") == Path("/data/P1/S1/SE1/seg.nrrd")

    def test_cross_level_resolution(self):
        fd = _make_file_def(pattern="model.onnx", level=DicomQueryLevel.PATIENT)
        dirs = {
            DicomQueryLevel.PATIENT: Path("/data/P1"),
            DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1"),
        }
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={},
        )
        assert resolver.resolve(fd) == Path("/data/P1/model.onnx")

    def test_unknown_name_raises(self):
        dirs = {DicomQueryLevel.SERIES: Path("/data/P1/S1/SE1")}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[],
            fields={},
        )
        with pytest.raises(KeyError):
            resolver.resolve("nonexistent")


# ── FileResolver.exists ─────────────────────────────────────────────────────


class TestFileResolverExists:
    """Tests for FileResolver.exists()."""

    def test_exists_true(self, tmp_path: Path):
        (tmp_path / "seg.nrrd").touch()
        fd = _make_file_def(pattern="seg.nrrd")
        dirs = {DicomQueryLevel.SERIES: tmp_path}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={},
        )
        assert resolver.exists(fd) is True

    def test_exists_false(self, tmp_path: Path):
        fd = _make_file_def(pattern="seg.nrrd")
        dirs = {DicomQueryLevel.SERIES: tmp_path}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={},
        )
        assert resolver.exists(fd) is False


# ── FileResolver.glob ────────────────────────────────────────────────────────


class TestFileResolverGlob:
    """Tests for FileResolver.glob()."""

    def test_glob_multiple(self, tmp_path: Path):
        (tmp_path / "slice_001.dcm").touch()
        (tmp_path / "slice_002.dcm").touch()
        (tmp_path / "other.txt").touch()
        fd = _make_file_def(name="slices", pattern="slice_{n}.dcm", multiple=True)
        dirs = {DicomQueryLevel.SERIES: tmp_path}
        resolver = FileResolver(
            working_dirs=dirs,
            record_type_level=DicomQueryLevel.SERIES,
            file_registry=[fd],
            fields={},
        )
        result = resolver.glob(fd)
        assert len(result) == 2
        assert all("slice_" in p.name for p in result)


# ── RecordQuery ──────────────────────────────────────────────────────────────


class TestRecordQuery:
    """Tests for RecordQuery."""

    @pytest.mark.asyncio
    async def test_find_delegates_to_client(self):
        client = AsyncMock()
        client.find_records_advanced = AsyncMock(return_value=[])
        files = MagicMock(spec=FileResolver)
        rq = RecordQuery(client=client, files=files)

        result = await rq.find("ct_seg", series_uid="1.2.3")

        assert result == []
        client.find_records_advanced.assert_awaited_once_with(
            record_type_name="ct_seg",
            series_uid="1.2.3",
            study_uid=None,
            patient_id=None,
            record_status=None,
            limit=100,
        )

    @pytest.mark.asyncio
    @patch("src.services.pipeline.context.settings")
    async def test_file_path_happy(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        fd = _make_file_def(name="segmentation", pattern="seg.nrrd")
        record = _make_record_read(file_registry=[fd], file_links=None)

        client = AsyncMock()
        client.find_records_advanced = AsyncMock(return_value=[record])
        files = MagicMock(spec=FileResolver)
        rq = RecordQuery(client=client, files=files)

        result = await rq.file_path("ct_seg", file="segmentation", series_uid="1.2.3")

        assert result == Path("/data/CLARINET_1/9.8.7.6.5/9.8.7.6.5.4/seg.nrrd")

    @pytest.mark.asyncio
    async def test_file_path_no_record_raises(self):
        client = AsyncMock()
        client.find_records_advanced = AsyncMock(return_value=[])
        files = MagicMock(spec=FileResolver)
        rq = RecordQuery(client=client, files=files)

        with pytest.raises(PipelineStepError, match="No record found"):
            await rq.file_path("ct_seg", file="segmentation", series_uid="1.2.3")

    @pytest.mark.asyncio
    @patch("src.services.pipeline.context.settings")
    async def test_file_path_uses_file_links(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        fd = _make_file_def(name="segmentation", pattern="seg_{id}.nrrd")
        link = RecordFileLinkRead(name="segmentation", filename="seg_42.nrrd", checksum=None)
        record = _make_record_read(file_registry=[fd], file_links=[link])

        client = AsyncMock()
        client.find_records_advanced = AsyncMock(return_value=[record])
        files = MagicMock(spec=FileResolver)
        rq = RecordQuery(client=client, files=files)

        result = await rq.file_path("ct_seg", file="segmentation", series_uid="1.2.3")

        # Should use the filename from file_links, not resolve pattern
        assert result == Path("/data/CLARINET_1/9.8.7.6.5/9.8.7.6.5.4/seg_42.nrrd")


# ── build_task_context ───────────────────────────────────────────────────────


class TestBuildTaskContext:
    """Tests for build_task_context."""

    @pytest.mark.asyncio
    @patch("src.services.pipeline.context.settings")
    async def test_from_record_id(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        fd = _make_file_def(name="seg", pattern="seg.nrrd")
        record = _make_record_read(record_id=42, file_registry=[fd])

        client = AsyncMock()
        client.get_record = AsyncMock(return_value=record)
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3.4.5", record_id=42)

        ctx = await build_task_context(msg, client)

        client.get_record.assert_awaited_once_with(42)
        assert isinstance(ctx, TaskContext)
        assert isinstance(ctx.files, FileResolver)
        assert isinstance(ctx.records, RecordQuery)
        assert ctx.client is client
        assert ctx.msg is msg

    @pytest.mark.asyncio
    @patch("src.services.pipeline.context.settings")
    async def test_from_series_uid(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        series = MagicMock()
        series.series_uid = "1.2.3.4.5.6"
        series.anon_uid = "9.8.7.6.5.4"
        series.study = MagicMock()
        series.study.study_uid = "1.2.3.4.5"
        series.study.anon_uid = "9.8.7.6.5"
        series.study.patient = _make_patient()

        client = AsyncMock()
        client.get_series = AsyncMock(return_value=series)
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3.4.5", series_uid="1.2.3.4.5.6")

        ctx = await build_task_context(msg, client)

        client.get_series.assert_awaited_once_with("1.2.3.4.5.6")
        assert DicomQueryLevel.SERIES in ctx.files._working_dirs

    @pytest.mark.asyncio
    @patch("src.services.pipeline.context.settings")
    async def test_from_study_uid(self, mock_settings: MagicMock):
        mock_settings.storage_path = "/data"
        study = MagicMock()
        study.study_uid = "1.2.3.4.5"
        study.anon_uid = "9.8.7.6.5"
        study.patient = _make_patient()

        client = AsyncMock()
        client.get_study = AsyncMock(return_value=study)
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3.4.5")

        ctx = await build_task_context(msg, client)

        client.get_study.assert_awaited_once_with("1.2.3.4.5")
        assert DicomQueryLevel.PATIENT in ctx.files._working_dirs
        assert DicomQueryLevel.STUDY in ctx.files._working_dirs
        assert DicomQueryLevel.SERIES not in ctx.files._working_dirs

    @pytest.mark.asyncio
    async def test_empty_context(self):
        """When no IDs present, context is minimal but valid."""
        client = AsyncMock()
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3.4.5")
        # Clear all optional fields
        msg = msg.model_copy(update={"series_uid": None, "record_id": None})
        # study_uid is present but record_id and series_uid are None
        # so fallback to study_uid branch
        study = MagicMock()
        study.study_uid = "1.2.3.4.5"
        study.anon_uid = None
        study.patient = _make_patient(anon_id=None)
        client.get_study = AsyncMock(return_value=study)

        ctx = await build_task_context(msg, client)

        assert isinstance(ctx, TaskContext)


# ── pipeline_task decorator ──────────────────────────────────────────────────


class TestPipelineTaskDecorator:
    """Tests for pipeline_task() decorator."""

    @pytest.mark.asyncio
    @patch("src.services.pipeline.task.get_broker")
    @patch("src.services.pipeline.task.register_task")
    @patch("src.services.pipeline.task.build_task_context")
    @patch("src.services.pipeline.task.ClarinetClient")
    @patch("src.services.pipeline.task.settings")
    async def test_calls_function(
        self,
        mock_settings: MagicMock,
        mock_client_cls: MagicMock,
        mock_build_ctx: MagicMock,
        mock_register: MagicMock,
        mock_get_broker: MagicMock,
    ):
        mock_settings.host = "localhost"
        mock_settings.port = 8000
        mock_settings.admin_email = "admin@test.com"
        mock_settings.admin_password = "pass"

        # Mock broker
        mock_broker = MagicMock()
        mock_task_decorator = MagicMock(side_effect=lambda fn: fn)
        mock_broker.task = MagicMock(return_value=mock_task_decorator)
        mock_get_broker.return_value = mock_broker

        # Mock client
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client

        # Mock context with files supporting snapshot_checksums and accessed_files
        mock_files = MagicMock()
        mock_files.snapshot_checksums = AsyncMock(return_value={})
        mock_files.accessed_files = {}
        mock_ctx = MagicMock()
        mock_ctx.files = mock_files
        mock_build_ctx.return_value = mock_ctx

        # Import after mocking
        from src.services.pipeline.task import pipeline_task

        called_with: dict = {}

        @pipeline_task()
        async def my_task(msg: MagicMock, ctx: MagicMock) -> None:
            called_with["msg"] = msg
            called_with["ctx"] = ctx

        msg_dict = {"patient_id": "PAT001", "study_uid": "1.2.3"}
        result = await my_task(msg_dict)

        assert called_with["ctx"] is mock_ctx
        assert isinstance(result, dict)
        assert result["patient_id"] == "PAT001"
        mock_client.login.assert_awaited_once()
        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("src.services.pipeline.task.get_broker")
    @patch("src.services.pipeline.task.register_task")
    @patch("src.services.pipeline.task.build_task_context")
    @patch("src.services.pipeline.task.ClarinetClient")
    @patch("src.services.pipeline.task.settings")
    async def test_returns_custom_message(
        self,
        mock_settings: MagicMock,
        mock_client_cls: MagicMock,
        mock_build_ctx: MagicMock,
        mock_register: MagicMock,
        mock_get_broker: MagicMock,
    ):
        mock_settings.host = "localhost"
        mock_settings.port = 8000
        mock_settings.admin_email = "admin@test.com"
        mock_settings.admin_password = "pass"

        mock_broker = MagicMock()
        mock_task_decorator = MagicMock(side_effect=lambda fn: fn)
        mock_broker.task = MagicMock(return_value=mock_task_decorator)
        mock_get_broker.return_value = mock_broker

        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_files = MagicMock()
        mock_files.snapshot_checksums = AsyncMock(return_value={})
        mock_files.accessed_files = {}
        mock_ctx = MagicMock()
        mock_ctx.files = mock_files
        mock_build_ctx.return_value = mock_ctx

        from src.services.pipeline.task import pipeline_task

        @pipeline_task()
        async def my_task(msg: PipelineMessage, ctx: MagicMock) -> PipelineMessage:
            return msg.model_copy(update={"payload": {"result": "ok"}})

        msg_dict = {"patient_id": "PAT001", "study_uid": "1.2.3"}
        result = await my_task(msg_dict)

        assert result["payload"] == {"result": "ok"}

    @pytest.mark.asyncio
    @patch("src.services.pipeline.task.get_broker")
    @patch("src.services.pipeline.task.register_task")
    @patch("src.services.pipeline.task.build_task_context")
    @patch("src.services.pipeline.task.ClarinetClient")
    @patch("src.services.pipeline.task.settings")
    async def test_closes_client_on_error(
        self,
        mock_settings: MagicMock,
        mock_client_cls: MagicMock,
        mock_build_ctx: MagicMock,
        mock_register: MagicMock,
        mock_get_broker: MagicMock,
    ):
        mock_settings.host = "localhost"
        mock_settings.port = 8000
        mock_settings.admin_email = "admin@test.com"
        mock_settings.admin_password = "pass"

        mock_broker = MagicMock()
        mock_task_decorator = MagicMock(side_effect=lambda fn: fn)
        mock_broker.task = MagicMock(return_value=mock_task_decorator)
        mock_get_broker.return_value = mock_broker

        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_files = MagicMock()
        mock_files.snapshot_checksums = AsyncMock(return_value={})
        mock_files.accessed_files = {}
        mock_ctx = MagicMock()
        mock_ctx.files = mock_files
        mock_build_ctx.return_value = mock_ctx

        from src.services.pipeline.task import pipeline_task

        @pipeline_task()
        async def failing_task(msg: MagicMock, ctx: MagicMock) -> None:
            raise ValueError("Something went wrong")

        msg_dict = {"patient_id": "PAT001", "study_uid": "1.2.3"}
        with pytest.raises(ValueError, match="Something went wrong"):
            await failing_task(msg_dict)

        mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("src.services.pipeline.task.get_broker")
    @patch("src.services.pipeline.task.register_task")
    @patch("src.services.pipeline.task.build_task_context")
    @patch("src.services.pipeline.task.ClarinetClient")
    @patch("src.services.pipeline.task.settings")
    async def test_propagates_errors(
        self,
        mock_settings: MagicMock,
        mock_client_cls: MagicMock,
        mock_build_ctx: MagicMock,
        mock_register: MagicMock,
        mock_get_broker: MagicMock,
    ):
        mock_settings.host = "localhost"
        mock_settings.port = 8000
        mock_settings.admin_email = "admin@test.com"
        mock_settings.admin_password = "pass"

        mock_broker = MagicMock()
        mock_task_decorator = MagicMock(side_effect=lambda fn: fn)
        mock_broker.task = MagicMock(return_value=mock_task_decorator)
        mock_get_broker.return_value = mock_broker

        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        mock_files = MagicMock()
        mock_files.snapshot_checksums = AsyncMock(return_value={})
        mock_files.accessed_files = {}
        mock_ctx = MagicMock()
        mock_ctx.files = mock_files
        mock_build_ctx.return_value = mock_ctx

        from src.services.pipeline.task import pipeline_task

        @pipeline_task()
        async def error_task(msg: MagicMock, ctx: MagicMock) -> None:
            raise PipelineStepError("error_task", "boom")

        msg_dict = {"patient_id": "PAT001", "study_uid": "1.2.3"}
        with pytest.raises(PipelineStepError, match="boom"):
            await error_task(msg_dict)
