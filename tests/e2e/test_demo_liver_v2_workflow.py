"""E2E tests for demo_liver_v2 workflow scenarios.

Simulates the complete liver study workflow including:
- Study arrival -> first-check -> anonymization -> segmentation
- Conditional branching by study_type (CT/MRI/CT-AG/etc.)
- Segmentation -> projection + comparison creation
- Comparison outcomes (false positive/negative) -> downstream records
- Master model file update -> projection invalidation cascade
- Late-stage chain: MDK -> resection-model -> resection-plan
- Intraop protocol with additional lesion detection
- Blocking/unblocking based on input file availability
- Record status constraints and error handling
- Direct invalidation (hard/soft modes)
- Multi-study per patient scenarios

Pipeline tasks are mocked (no RabbitMQ). Slicer actions are simulated
by directly submitting data and creating files via image service.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.api.app import app
from clarinet.client import ClarinetClient
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinition, FileRole, RecordTypeFileLink
from clarinet.models.record import RecordType
from clarinet.models.study import Series, Study
from clarinet.services.pipeline import PipelineMessage, pipeline_task
from clarinet.services.pipeline.chain import _PIPELINE_REGISTRY, _TASK_REGISTRY
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.recordflow.flow_file import FILE_REGISTRY, file
from clarinet.services.recordflow.flow_record import (
    ENTITY_REGISTRY,
    RECORD_REGISTRY,
    record,
    study,
)
from clarinet.services.recordflow.flow_result import Field
from tests.utils.factories import make_patient
from tests.utils.urls import RECORDS_BASE, RECORDS_FIND

pytestmark = pytest.mark.asyncio

F = Field()


# ============================================================================
# Dummy pipeline tasks (for do_task registration — never actually executed)
# ============================================================================


@pipeline_task()
def _dummy_anonymize(_msg: PipelineMessage, _ctx: Any) -> None:
    """Placeholder for anonymize_study_pipeline."""


@pipeline_task()
def _dummy_init_master(_msg: PipelineMessage, _ctx: Any) -> None:
    """Placeholder for init_master_model."""


@pipeline_task()
async def _dummy_auto_project(_msg: PipelineMessage, _ctx: Any) -> None:
    """Placeholder for auto_project_ct."""


@pipeline_task(auto_submit=True)
def _dummy_compare(_msg: PipelineMessage, _ctx: Any) -> dict:
    """Placeholder for compare_w_projection."""
    return {}


# ============================================================================
# Callback functions (replicated from demo — executed by engine via call())
# ============================================================================


async def create_projection_record(
    record: Any, context: dict[str, Any], client: ClarinetClient
) -> None:
    """Create create-master-projection for the segmentation's best_series."""
    first_checks = await client.find_records(
        record_type_name="first-check", study_uid=record.study_uid
    )
    if not first_checks:
        return
    best_series = (first_checks[0].data or {}).get("best_series")
    if not best_series:
        return

    from clarinet.models import RecordCreate

    await client.create_record(
        RecordCreate(
            record_type_name="create-master-projection",
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=best_series,
            context_info=f"From {record.record_type.name} (id={record.id})",
        )
    )


async def create_comparison_record(
    record: Any, context: dict[str, Any], client: ClarinetClient
) -> None:
    """Create compare-with-projection linked to segmentation as parent."""
    first_checks = await client.find_records(
        record_type_name="first-check", study_uid=record.study_uid
    )
    best_series = (first_checks[0].data or {}).get("best_series") if first_checks else None
    if not best_series:
        return

    from clarinet.models import RecordCreate

    await client.create_record(
        RecordCreate(
            record_type_name="compare-with-projection",
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=best_series,
            parent_record_id=record.id,
            context_info=f"From {record.record_type.name} (id={record.id})",
        )
    )


async def unblock_comparisons(record: Any, context: dict[str, Any], client: ClarinetClient) -> None:
    """Check-files on blocked compare-with-projection for this series."""
    comparisons = await client.find_records(series_uid=record.series_uid, record_status="blocked")
    for comp in comparisons:
        await client.check_record_files(comp.id)


async def create_second_review_record(
    record: Any, context: dict[str, Any], client: ClarinetClient
) -> None:
    """Create second-review linked to parent segmentation for {user_id} resolution."""
    from clarinet.models import RecordCreate

    await client.create_record(
        RecordCreate(
            record_type_name="second-review",
            patient_id=record.patient_id,
            study_uid=record.study_uid,
            series_uid=record.series_uid,
            parent_record_id=record.parent_record_id,
            context_info=f"From compare-with-projection (id={record.id})",
        )
    )


async def unblock_second_reviews(
    record: Any, context: dict[str, Any], client: ClarinetClient
) -> None:
    """Check-files on blocked second-review for this series."""
    reviews = await client.find_records(
        record_type_name="second-review",
        series_uid=record.series_uid,
        record_status="blocked",
    )
    for review in reviews:
        await client.check_record_files(review.id)


# ============================================================================
# Flow DSL registration (mirrors demo_liver_v2/tasks/workflows/pipeline_flow.py)
# ============================================================================


def register_demo_flows() -> None:
    """Register all demo_liver_v2 workflow flows."""
    # Study arrival -> first-check
    study().on_creation().create_record("first-check")

    # first-check -> anonymize-study (only if is_good)
    record("first-check").on_finished().if_record(F.is_good == True).create_record(  # noqa: E712
        "anonymize-study"
    )

    # anonymize-study pending -> dispatch anonymization pipeline
    record("anonymize-study").on_status("pending").do_task(_dummy_anonymize, send_to_pacs=True)

    # anonymize-study finished -> branch by study_type
    (
        record("anonymize-study")
        .on_finished()
        .match(F.study_type)
        .case("CT")
        .create_record("segment-ct-single", "segment-ct-with-archive")
        .case("MRI")
        .create_record("segment-mri-single")
        .case("CT-AG")
        .create_record("segment-ctag-single")
        .case("MRI-AG")
        .create_record("segment-mriag-single")
        .case("PDCT-AG")
        .create_record("segment-pdctag-single")
    )

    # Segmentation finished -> projection + comparison for all single-modality types
    for seg_type in [
        "segment-ct-single",
        "segment-mri-single",
        "segment-ctag-single",
        "segment-mriag-single",
        "segment-pdctag-single",
    ]:
        record(seg_type).on_finished().call(create_projection_record)
        record(seg_type).on_finished().call(create_comparison_record)

    # CT with archive also creates projection, comparison, and dispatches master model init
    record("segment-ct-with-archive").on_finished().call(create_projection_record)
    record("segment-ct-with-archive").on_finished().call(create_comparison_record)
    record("segment-ct-with-archive").on_finished().do_task(_dummy_init_master)

    # Auto-projection for CT on pending
    record("create-master-projection").on_status("pending").do_task(_dummy_auto_project)

    # Projection finished -> unblock comparisons and second reviews
    record("create-master-projection").on_finished().call(unblock_comparisons)
    record("create-master-projection").on_finished().call(unblock_second_reviews)

    # Auto-compare on pending
    record("compare-with-projection").on_status("pending").do_task(_dummy_compare)

    # Comparison finished -> downstream records
    (
        record("compare-with-projection")
        .on_finished()
        .if_record(F.false_positive_num > 0)
        .create_record("update-master-model")
    )
    (
        record("compare-with-projection")
        .on_finished()
        .if_record(F.false_negative_num > 0)
        .call(create_second_review_record)
    )

    # Master model file update -> invalidate all projections
    file("master_model").on_update().invalidate_all_records("create-master-projection")

    # Late stages
    record("mdk-conclusion").on_finished().create_record("resection-model")
    record("resection-model").on_finished().create_record("resection-plan")
    (
        record("intraop-protocol")
        .on_finished()
        .if_record(F.additionally_found > 0)
        .create_record("update-master-model")
    )


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def _clear_registries():
    """Clear all registries before and after each test."""
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    _TASK_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    _TASK_REGISTRY.clear()


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Authenticated superuser client (overrides e2e conftest unauthenticated client)."""
    from tests.conftest import create_authenticated_client, create_mock_superuser

    mock_user = await create_mock_superuser(test_session, email="liver_demo@test.com")
    async for ac in create_authenticated_client(mock_user, test_session, test_settings):
        yield ac


@pytest_asyncio.fixture
async def hierarchy(test_session: AsyncSession) -> dict[str, str]:
    """Create patient -> study -> series hierarchy."""
    patient = make_patient("LIVER_PAT_001", "Test Patient")
    test_session.add(patient)
    await test_session.commit()

    study_obj = Study(
        study_uid="1.2.840.113619.2.5.100",
        patient_id="LIVER_PAT_001",
        date=datetime.now(tz=UTC).date(),
    )
    test_session.add(study_obj)
    await test_session.commit()

    series_obj = Series(
        series_uid="1.2.840.113619.2.5.100.1",
        series_number=1,
        study_uid="1.2.840.113619.2.5.100",
    )
    test_session.add(series_obj)
    await test_session.commit()

    return {
        "patient_id": "LIVER_PAT_001",
        "study_uid": "1.2.840.113619.2.5.100",
        "series_uid": "1.2.840.113619.2.5.100.1",
    }


@pytest_asyncio.fixture
async def record_types(test_session: AsyncSession) -> dict[str, RecordType]:
    """Create all demo_liver_v2 record types (without file definitions)."""
    types_data = [
        ("first-check", DicomQueryLevel.STUDY, 2),
        ("anonymize-study", DicomQueryLevel.STUDY, 1),
        ("segment-ct-single", DicomQueryLevel.STUDY, 4),
        ("segment-ct-with-archive", DicomQueryLevel.STUDY, 4),
        ("segment-mri-single", DicomQueryLevel.STUDY, 4),
        ("segment-mriag-single", DicomQueryLevel.STUDY, 4),
        ("segment-ctag-single", DicomQueryLevel.STUDY, 4),
        ("segment-pdctag-single", DicomQueryLevel.STUDY, 4),
        ("create-master-projection", DicomQueryLevel.SERIES, 1),
        ("compare-with-projection", DicomQueryLevel.SERIES, 4),
        ("second-review", DicomQueryLevel.SERIES, 1),
        ("update-master-model", DicomQueryLevel.PATIENT, 1),
        ("mdk-conclusion", DicomQueryLevel.PATIENT, 1),
        ("resection-model", DicomQueryLevel.PATIENT, 1),
        ("resection-plan", DicomQueryLevel.PATIENT, 1),
        ("intraop-protocol", DicomQueryLevel.PATIENT, 1),
    ]
    types: dict[str, RecordType] = {}
    for name, level, max_records in types_data:
        rt = RecordType(
            name=name,
            description=f"Demo: {name}",
            level=level,
            max_records=max_records,
        )
        test_session.add(rt)
        types[name] = rt
    await test_session.commit()
    for rt in types.values():
        await test_session.refresh(rt)
    return types


@pytest_asyncio.fixture
async def flow_engine(clarinet_client: ClarinetClient) -> RecordFlowEngine:
    """Create engine and register demo workflow flows."""
    engine = RecordFlowEngine(clarinet_client)
    register_demo_flows()
    for flow_rec in RECORD_REGISTRY:
        if flow_rec.is_active_flow():
            engine.register_flow(flow_rec)
    for entity_flow in ENTITY_REGISTRY:
        engine.register_flow(entity_flow)
    for file_flow in FILE_REGISTRY:
        if file_flow.is_active_flow():
            engine.register_flow(file_flow)
    return engine


@pytest_asyncio.fixture
async def app_with_engine(
    flow_engine: RecordFlowEngine,
) -> AsyncGenerator[RecordFlowEngine]:
    """Install engine in app.state for service-layer integration."""
    app.state.recordflow_engine = flow_engine
    yield flow_engine
    app.state.recordflow_engine = None


@pytest.fixture
def captured_pipelines():
    """Capture all pipeline dispatches without executing them."""
    captures: list[tuple[str, PipelineMessage]] = []

    async def mock_run_pipeline(
        self: RecordFlowEngine,
        action: Any,
        message: PipelineMessage,
        context: str,
    ) -> None:
        captures.append((action.pipeline_name, message))

    with patch.object(RecordFlowEngine, "_run_pipeline", mock_run_pipeline):
        yield captures


# ============================================================================
# API helper functions
# ============================================================================


async def _create_record(
    client: AsyncClient,
    record_type_name: str,
    patient_id: str,
    study_uid: str | None = None,
    series_uid: str | None = None,
    parent_record_id: int | None = None,
    context_info: str | None = None,
) -> dict[str, Any]:
    """Create a record via API."""
    body: dict[str, Any] = {
        "record_type_name": record_type_name,
        "patient_id": patient_id,
        "study_uid": study_uid,
        "series_uid": series_uid,
    }
    if parent_record_id is not None:
        body["parent_record_id"] = parent_record_id
    if context_info is not None:
        body["context_info"] = context_info
    resp = await client.post(f"{RECORDS_BASE}/", json=body)
    assert resp.status_code == 201, f"Create record failed: {resp.text}"
    return resp.json()


async def _submit_data(
    client: AsyncClient,
    record_id: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Submit data for a record (sets status to finished)."""
    resp = await client.post(f"{RECORDS_BASE}/{record_id}/data", json=data)
    assert resp.status_code == 200, f"Submit data failed: {resp.text}"
    return resp.json()


async def _find_records(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:
    """Find records by criteria."""
    resp = await client.post(RECORDS_FIND, json=params)
    assert resp.status_code == 200, f"Find records failed: {resp.text}"
    return resp.json()


async def _get_record(client: AsyncClient, record_id: int) -> dict[str, Any]:
    """Get a single record."""
    resp = await client.get(f"{RECORDS_BASE}/{record_id}")
    assert resp.status_code == 200
    return resp.json()


async def _invalidate_record(
    client: AsyncClient,
    record_id: int,
    mode: str = "hard",
    source_record_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Invalidate a record."""
    body: dict[str, Any] = {"mode": mode}
    if source_record_id is not None:
        body["source_record_id"] = source_record_id
    if reason is not None:
        body["reason"] = reason
    resp = await client.post(f"{RECORDS_BASE}/{record_id}/invalidate", json=body)
    assert resp.status_code == 200, f"Invalidate failed: {resp.text}"
    return resp.json()


async def _setup_through_anonymization(
    client: AsyncClient,
    hierarchy: dict[str, str],
    study_type: str,
) -> dict[str, Any]:
    """Create first-check + anonymize-study, submit both. Returns finished anonymization."""
    fc = await _create_record(
        client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
    )
    await _submit_data(
        client,
        fc["id"],
        {
            "is_good": True,
            "study_type": study_type,
            "best_series": hierarchy["series_uid"],
        },
    )
    anon = await _find_records(
        client, record_type_name="anonymize-study", study_uid=hierarchy["study_uid"]
    )
    assert len(anon) == 1
    result = await _submit_data(client, anon[0]["id"], {"study_type": study_type})
    return result


async def _setup_through_segmentation(
    client: AsyncClient,
    hierarchy: dict[str, str],
    seg_type: str = "segment-ct-single",
) -> dict[str, Any]:
    """Setup through anonymization, then submit a segmentation. Returns finished seg."""
    await _setup_through_anonymization(client, hierarchy, "CT")
    seg = await _find_records(client, record_type_name=seg_type, study_uid=hierarchy["study_uid"])
    assert len(seg) >= 1
    result = await _submit_data(client, seg[0]["id"], {})
    return result


# ============================================================================
# Tests: First-Check Workflow
# ============================================================================


class TestFirstCheckWorkflow:
    """First-check creation and downstream triggers."""

    async def test_good_first_check_creates_anonymization(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """first-check with is_good=True -> anonymize-study is created."""
        fc = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(
            client,
            fc["id"],
            {"is_good": True, "study_type": "CT", "best_series": hierarchy["series_uid"]},
        )

        anon = await _find_records(
            client, record_type_name="anonymize-study", study_uid=hierarchy["study_uid"]
        )
        assert len(anon) == 1
        assert anon[0]["status"] == "pending"

        # Pipeline was dispatched for anonymize-study
        assert any("_dummy_anonymize" in name for name, _ in captured_pipelines)

    async def test_bad_first_check_no_downstream(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """first-check with is_good=False -> no anonymize-study."""
        fc = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(client, fc["id"], {"is_good": False})

        anon = await _find_records(
            client, record_type_name="anonymize-study", study_uid=hierarchy["study_uid"]
        )
        assert len(anon) == 0

    async def test_max_records_constraint(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """first-check has max_records=2; third creation fails with 409."""
        for _ in range(2):
            await _create_record(
                client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
            )

        resp = await client.post(
            f"{RECORDS_BASE}/",
            json={
                "record_type_name": "first-check",
                "patient_id": hierarchy["patient_id"],
                "study_uid": hierarchy["study_uid"],
            },
        )
        assert resp.status_code == 409


# ============================================================================
# Tests: Anonymization Branching by study_type
# ============================================================================


class TestAnonymizationBranching:
    """Anonymization completion triggers study_type-specific segmentations."""

    async def test_ct_creates_ct_segmentations(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """CT study -> segment-ct-single + segment-ct-with-archive."""
        await _setup_through_anonymization(client, hierarchy, "CT")

        ct_singles = await _find_records(
            client, record_type_name="segment-ct-single", study_uid=hierarchy["study_uid"]
        )
        ct_archives = await _find_records(
            client,
            record_type_name="segment-ct-with-archive",
            study_uid=hierarchy["study_uid"],
        )
        assert len(ct_singles) == 1
        assert len(ct_archives) == 1

    async def test_mri_creates_mri_segmentation(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """MRI study -> segment-mri-single only (no CT segmentations)."""
        await _setup_through_anonymization(client, hierarchy, "MRI")

        mri = await _find_records(
            client, record_type_name="segment-mri-single", study_uid=hierarchy["study_uid"]
        )
        assert len(mri) == 1

        ct = await _find_records(
            client, record_type_name="segment-ct-single", study_uid=hierarchy["study_uid"]
        )
        assert len(ct) == 0

    async def test_ctag_creates_ctag_segmentation(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """CT-AG study -> segment-ctag-single."""
        await _setup_through_anonymization(client, hierarchy, "CT-AG")

        ctag = await _find_records(
            client, record_type_name="segment-ctag-single", study_uid=hierarchy["study_uid"]
        )
        assert len(ctag) == 1

    async def test_mriag_creates_mriag_segmentation(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """MRI-AG study -> segment-mriag-single."""
        await _setup_through_anonymization(client, hierarchy, "MRI-AG")

        mriag = await _find_records(
            client,
            record_type_name="segment-mriag-single",
            study_uid=hierarchy["study_uid"],
        )
        assert len(mriag) == 1

    async def test_pdctag_creates_pdctag_segmentation(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """PDCT-AG study -> segment-pdctag-single."""
        await _setup_through_anonymization(client, hierarchy, "PDCT-AG")

        pdctag = await _find_records(
            client,
            record_type_name="segment-pdctag-single",
            study_uid=hierarchy["study_uid"],
        )
        assert len(pdctag) == 1


# ============================================================================
# Tests: Segmentation Downstream Triggers
# ============================================================================


class TestSegmentationDownstream:
    """Segmentation completion triggers projection and comparison."""

    async def test_ct_single_creates_projection_and_comparison(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Finishing segment-ct-single creates projection + comparison records."""
        await _setup_through_segmentation(client, hierarchy)

        projections = await _find_records(
            client,
            record_type_name="create-master-projection",
            series_uid=hierarchy["series_uid"],
        )
        assert len(projections) >= 1

        comparisons = await _find_records(
            client,
            record_type_name="compare-with-projection",
            series_uid=hierarchy["series_uid"],
        )
        assert len(comparisons) >= 1

        # Comparison parent_record_id points to segmentation
        seg = await _find_records(
            client, record_type_name="segment-ct-single", study_uid=hierarchy["study_uid"]
        )
        assert comparisons[0]["parent_record_id"] == seg[0]["id"]

    async def test_ct_with_archive_dispatches_master_model_init(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """segment-ct-with-archive finished dispatches init_master_model pipeline."""
        await _setup_through_segmentation(client, hierarchy, "segment-ct-with-archive")

        pipeline_names = [name for name, _ in captured_pipelines]
        assert any("_dummy_init_master" in name for name in pipeline_names)

        # Also creates projection and comparison
        projections = await _find_records(
            client,
            record_type_name="create-master-projection",
            series_uid=hierarchy["series_uid"],
        )
        assert len(projections) >= 1

    async def test_mri_single_creates_projection_and_comparison(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """MRI segmentation finished also creates projection + comparison."""
        # Setup through MRI anonymization
        await _setup_through_anonymization(client, hierarchy, "MRI")
        seg = await _find_records(
            client, record_type_name="segment-mri-single", study_uid=hierarchy["study_uid"]
        )
        await _submit_data(client, seg[0]["id"], {})

        projections = await _find_records(
            client,
            record_type_name="create-master-projection",
            series_uid=hierarchy["series_uid"],
        )
        assert len(projections) >= 1


# ============================================================================
# Tests: Projection Workflow
# ============================================================================


class TestProjectionWorkflow:
    """Projection creation and auto-project dispatch."""

    async def test_projection_pending_dispatches_auto_project(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Creating a projection record dispatches auto_project_ct pipeline."""
        await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )

        assert any("_dummy_auto_project" in name for name, _ in captured_pipelines)

    async def test_projection_finished_dispatches_unblock(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Finishing a projection triggers unblock callbacks (no error)."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        result = await _submit_data(client, proj["id"], {})
        assert result["status"] == "finished"

    async def test_comparison_pending_dispatches_auto_compare(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Creating a comparison record dispatches auto-compare pipeline."""
        # Need a segmentation as parent first
        seg = await _create_record(
            client, "segment-ct-single", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _create_record(
            client,
            "compare-with-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
            parent_record_id=seg["id"],
        )

        assert any("_dummy_compare" in name for name, _ in captured_pipelines)


# ============================================================================
# Tests: Comparison Result Branching
# ============================================================================


class TestComparisonResults:
    """Comparison outcome branching: false positive/negative -> downstream records."""

    async def _get_comparison(
        self, client: AsyncClient, hierarchy: dict[str, str]
    ) -> dict[str, Any]:
        """Setup through segmentation and return the comparison record."""
        await _setup_through_segmentation(client, hierarchy)
        comp = await _find_records(
            client,
            record_type_name="compare-with-projection",
            series_uid=hierarchy["series_uid"],
        )
        assert len(comp) >= 1
        return comp[0]

    async def test_false_positive_creates_update_master_model(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Comparison with false_positive_num > 0 -> update-master-model."""
        comp = await self._get_comparison(client, hierarchy)
        await _submit_data(
            client,
            comp["id"],
            {"false_negative": [], "false_negative_num": 0, "false_positive_num": 2},
        )

        updates = await _find_records(
            client,
            record_type_name="update-master-model",
            patient_id=hierarchy["patient_id"],
        )
        assert len(updates) == 1

    async def test_false_negative_creates_second_review(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Comparison with false_negative_num > 0 -> second-review."""
        comp = await self._get_comparison(client, hierarchy)
        await _submit_data(
            client,
            comp["id"],
            {
                "false_negative": [{"lesion_num": 1}, {"lesion_num": 2}],
                "false_negative_num": 2,
                "false_positive_num": 0,
            },
        )

        reviews = await _find_records(
            client,
            record_type_name="second-review",
            series_uid=hierarchy["series_uid"],
        )
        assert len(reviews) == 1

        # second-review parent should be the segmentation, not the comparison
        seg = await _find_records(
            client, record_type_name="segment-ct-single", study_uid=hierarchy["study_uid"]
        )
        assert reviews[0]["parent_record_id"] == seg[0]["id"]

    async def test_both_false_positive_and_negative(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Both false_positive and false_negative -> both downstream records."""
        comp = await self._get_comparison(client, hierarchy)
        await _submit_data(
            client,
            comp["id"],
            {
                "false_negative": [{"lesion_num": 3}],
                "false_negative_num": 1,
                "false_positive_num": 1,
            },
        )

        updates = await _find_records(
            client,
            record_type_name="update-master-model",
            patient_id=hierarchy["patient_id"],
        )
        reviews = await _find_records(
            client,
            record_type_name="second-review",
            series_uid=hierarchy["series_uid"],
        )
        assert len(updates) == 1
        assert len(reviews) == 1

    async def test_no_difference_no_downstream(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Comparison with all zeros -> no downstream records."""
        comp = await self._get_comparison(client, hierarchy)
        await _submit_data(
            client,
            comp["id"],
            {"false_negative": [], "false_negative_num": 0, "false_positive_num": 0},
        )

        updates = await _find_records(
            client,
            record_type_name="update-master-model",
            patient_id=hierarchy["patient_id"],
        )
        reviews = await _find_records(
            client,
            record_type_name="second-review",
            series_uid=hierarchy["series_uid"],
        )
        assert len(updates) == 0
        assert len(reviews) == 0


# ============================================================================
# Tests: Master Model Invalidation
# ============================================================================


class TestMasterModelInvalidation:
    """File-level triggers: master model update invalidates projections."""

    async def test_file_update_invalidates_finished_projection(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """master_model file update invalidates all create-master-projection records."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        await _submit_data(client, proj["id"], {})

        proj_data = await _get_record(client, proj["id"])
        assert proj_data["status"] == "finished"

        # Trigger file update event
        await app_with_engine.handle_file_update("master_model", hierarchy["patient_id"])

        proj_after = await _get_record(client, proj["id"])
        assert proj_after["status"] == "pending"

    async def test_invalidation_appends_context_info(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Invalidation appends reason to context_info."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
            context_info="Original context",
        )
        await _submit_data(client, proj["id"], {})

        await app_with_engine.handle_file_update("master_model", hierarchy["patient_id"])

        proj_after = await _get_record(client, proj["id"])
        assert proj_after["context_info"] is not None
        assert "Original context" in proj_after["context_info"]

    async def test_invalidation_re_dispatches_auto_project(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Invalidated projection (back to pending) re-dispatches auto_project pipeline."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        await _submit_data(client, proj["id"], {})
        captured_pipelines.clear()

        await app_with_engine.handle_file_update("master_model", hierarchy["patient_id"])

        # on_status("pending") flow should re-dispatch auto_project
        assert any("_dummy_auto_project" in name for name, _ in captured_pipelines)


# ============================================================================
# Tests: Late Stage Chain (MDK -> resection -> intraop)
# ============================================================================


class TestLateStageChain:
    """Late-stage workflow: MDK -> resection -> intraop."""

    async def test_mdk_creates_resection_model(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """mdk-conclusion finished -> resection-model created."""
        mdk = await _create_record(client, "mdk-conclusion", hierarchy["patient_id"])
        await _submit_data(client, mdk["id"], {"treatment_plan": "cluster_removal"})

        resection = await _find_records(
            client,
            record_type_name="resection-model",
            patient_id=hierarchy["patient_id"],
        )
        assert len(resection) == 1

    async def test_resection_model_creates_plan(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """resection-model finished -> resection-plan created."""
        rm = await _create_record(client, "resection-model", hierarchy["patient_id"])
        await _submit_data(client, rm["id"], {})

        plans = await _find_records(
            client,
            record_type_name="resection-plan",
            patient_id=hierarchy["patient_id"],
        )
        assert len(plans) == 1

    async def test_mdk_to_resection_plan_full_chain(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Full chain: MDK -> resection-model -> finish -> resection-plan."""
        mdk = await _create_record(client, "mdk-conclusion", hierarchy["patient_id"])
        await _submit_data(client, mdk["id"], {})

        resection = await _find_records(
            client,
            record_type_name="resection-model",
            patient_id=hierarchy["patient_id"],
        )
        assert len(resection) == 1
        await _submit_data(client, resection[0]["id"], {})

        plans = await _find_records(
            client,
            record_type_name="resection-plan",
            patient_id=hierarchy["patient_id"],
        )
        assert len(plans) == 1

    async def test_intraop_additional_lesions_creates_update(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Intraop with additionally_found > 0 -> update-master-model."""
        intraop = await _create_record(client, "intraop-protocol", hierarchy["patient_id"])
        await _submit_data(client, intraop["id"], {"additionally_found": 2})

        updates = await _find_records(
            client,
            record_type_name="update-master-model",
            patient_id=hierarchy["patient_id"],
        )
        assert len(updates) == 1

    async def test_intraop_no_additional_no_update(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Intraop with additionally_found=0 -> no update-master-model."""
        intraop = await _create_record(client, "intraop-protocol", hierarchy["patient_id"])
        await _submit_data(client, intraop["id"], {"additionally_found": 0})

        updates = await _find_records(
            client,
            record_type_name="update-master-model",
            patient_id=hierarchy["patient_id"],
        )
        assert len(updates) == 0


# ============================================================================
# Tests: Record Status Constraints
# ============================================================================


class TestRecordStatusConstraints:
    """Record status transition constraints and error handling."""

    async def test_submit_finished_record_fails(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Submitting data on an already-finished record returns 409."""
        rec = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(client, rec["id"], {"is_good": False})

        resp = await client.post(f"{RECORDS_BASE}/{rec['id']}/data", json={"is_good": True})
        assert resp.status_code == 409

    async def test_update_data_on_finished_record(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """PATCH data on finished record is allowed."""
        rec = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(client, rec["id"], {"is_good": False})

        resp = await client.patch(
            f"{RECORDS_BASE}/{rec['id']}/data",
            json={"is_good": True, "study_type": "CT", "best_series": hierarchy["series_uid"]},
        )
        assert resp.status_code == 200

    async def test_update_data_on_non_finished_fails(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """PATCH data on pending record returns 409."""
        rec = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        resp = await client.patch(f"{RECORDS_BASE}/{rec['id']}/data", json={"is_good": True})
        assert resp.status_code == 409

    async def test_assign_user_transitions_to_inwork(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Assigning a user transitions record from pending to inwork."""
        from tests.conftest import create_mock_superuser

        user = await create_mock_superuser(test_session, email="doctor@test.com")

        rec = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        assert rec["status"] == "pending"

        resp = await client.patch(
            f"{RECORDS_BASE}/{rec['id']}/user",
            params={"user_id": str(user.id)},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "inwork"


# ============================================================================
# Tests: Direct Invalidation
# ============================================================================


class TestDirectInvalidation:
    """Test the invalidation endpoint directly."""

    async def test_hard_invalidation_resets_to_pending(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Hard invalidation of a finished record resets to pending."""
        rec = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(client, rec["id"], {"is_good": False})

        result = await _invalidate_record(
            client, rec["id"], mode="hard", reason="Test invalidation"
        )
        assert result["status"] == "pending"
        assert "Test invalidation" in (result["context_info"] or "")

    async def test_soft_invalidation_keeps_status(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Soft invalidation appends reason but keeps status unchanged."""
        rec = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(client, rec["id"], {"is_good": False})

        result = await _invalidate_record(client, rec["id"], mode="soft", reason="Soft note")
        assert result["status"] == "finished"
        assert "Soft note" in (result["context_info"] or "")

    async def test_hard_invalidation_re_triggers_flow(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Hard invalidation of anonymize-study re-dispatches its pending flow."""
        anon = await _create_record(
            client, "anonymize-study", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(client, anon["id"], {"study_type": "CT"})
        captured_pipelines.clear()

        await _invalidate_record(client, anon["id"], mode="hard")

        # Pipeline should be re-dispatched (on_status("pending") flow)
        assert any("_dummy_anonymize" in name for name, _ in captured_pipelines)

    async def test_multiple_invalidations_append_reasons(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Multiple invalidations append multiple reasons to context_info."""
        rec = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(client, rec["id"], {"is_good": False})

        await _invalidate_record(client, rec["id"], mode="soft", reason="Reason 1")
        result = await _invalidate_record(client, rec["id"], mode="soft", reason="Reason 2")

        assert "Reason 1" in result["context_info"]
        assert "Reason 2" in result["context_info"]


# ============================================================================
# Tests: Multi-Study Patient
# ============================================================================


class TestMultiStudyPatient:
    """Tests with multiple studies per patient."""

    @pytest_asyncio.fixture
    async def second_study(
        self, test_session: AsyncSession, hierarchy: dict[str, str]
    ) -> dict[str, str]:
        """Add a second study with series to the same patient."""
        study_obj = Study(
            study_uid="1.2.840.113619.2.5.200",
            patient_id=hierarchy["patient_id"],
            date=datetime.now(tz=UTC).date(),
        )
        test_session.add(study_obj)
        await test_session.commit()

        series_obj = Series(
            series_uid="1.2.840.113619.2.5.200.1",
            series_number=1,
            study_uid="1.2.840.113619.2.5.200",
        )
        test_session.add(series_obj)
        await test_session.commit()

        return {
            "study_uid": "1.2.840.113619.2.5.200",
            "series_uid": "1.2.840.113619.2.5.200.1",
        }

    async def test_different_study_types_create_different_segmentations(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        second_study: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Two studies (CT + MRI) create type-appropriate segmentations."""
        # Study 1: CT
        fc1 = await _create_record(
            client, "first-check", hierarchy["patient_id"], hierarchy["study_uid"]
        )
        await _submit_data(
            client,
            fc1["id"],
            {
                "is_good": True,
                "study_type": "CT",
                "best_series": hierarchy["series_uid"],
            },
        )
        anon1 = await _find_records(
            client, record_type_name="anonymize-study", study_uid=hierarchy["study_uid"]
        )
        await _submit_data(client, anon1[0]["id"], {"study_type": "CT"})

        # Study 2: MRI
        fc2 = await _create_record(
            client,
            "first-check",
            hierarchy["patient_id"],
            second_study["study_uid"],
        )
        await _submit_data(
            client,
            fc2["id"],
            {
                "is_good": True,
                "study_type": "MRI",
                "best_series": second_study["series_uid"],
            },
        )
        anon2 = await _find_records(
            client,
            record_type_name="anonymize-study",
            study_uid=second_study["study_uid"],
        )
        await _submit_data(client, anon2[0]["id"], {"study_type": "MRI"})

        # CT segmentations on study 1
        ct = await _find_records(
            client,
            record_type_name="segment-ct-single",
            study_uid=hierarchy["study_uid"],
        )
        assert len(ct) == 1

        # MRI segmentation on study 2
        mri = await _find_records(
            client,
            record_type_name="segment-mri-single",
            study_uid=second_study["study_uid"],
        )
        assert len(mri) == 1

        # No cross-contamination
        ct_on_mri = await _find_records(
            client,
            record_type_name="segment-ct-single",
            study_uid=second_study["study_uid"],
        )
        assert len(ct_on_mri) == 0

    async def test_invalidation_scoped_to_patient(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        second_study: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Master model invalidation affects projections for the same patient."""
        proj1 = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        await _submit_data(client, proj1["id"], {})

        proj2 = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            second_study["study_uid"],
            second_study["series_uid"],
        )
        await _submit_data(client, proj2["id"], {})

        # Trigger master model update for this patient
        await app_with_engine.handle_file_update("master_model", hierarchy["patient_id"])

        # Both projections should be invalidated
        p1 = await _get_record(client, proj1["id"])
        p2 = await _get_record(client, proj2["id"])
        assert p1["status"] == "pending"
        assert p2["status"] == "pending"


# ============================================================================
# Tests: Blocking / Unblocking
# ============================================================================


class TestBlockingMechanism:
    """File-based blocking and unblocking mechanism."""

    @pytest_asyncio.fixture
    async def file_record_types(
        self,
        test_session: AsyncSession,
        record_types: dict[str, RecordType],
    ) -> dict[str, RecordType]:
        """Add file definitions to create-master-projection for blocking tests."""
        master_fd = FileDefinition(
            name="master_model",
            pattern="master_model.seg.nrrd",
            level=DicomQueryLevel.PATIENT,
        )
        test_session.add(master_fd)
        await test_session.commit()
        await test_session.refresh(master_fd)

        link = RecordTypeFileLink(
            record_type_name="create-master-projection",
            file_definition_id=master_fd.id,
            role=FileRole.INPUT,
            required=True,
        )
        test_session.add(link)
        await test_session.commit()
        test_session.expire_all()

        return record_types

    async def test_record_blocked_when_input_missing(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        file_record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Record is blocked when required INPUT file doesn't exist."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        assert proj["status"] == "blocked"

    async def test_submit_on_blocked_record_fails(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        file_record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Cannot submit data on a blocked record."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        assert proj["status"] == "blocked"

        resp = await client.post(f"{RECORDS_BASE}/{proj['id']}/data", json={})
        assert resp.status_code == 409

    async def test_unblock_via_check_files(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        file_record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
        tmp_path: Path,
    ):
        """Blocked record transitions to pending after check-files finds the file."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        assert proj["status"] == "blocked"

        # WORKAROUND: mock validate_record_files instead of creating real files on disk.
        # Real file validation requires matching storage_path + patient/study/series directory
        # structure which is hard to set up in in-memory SQLite tests.
        from clarinet.services.file_validation import FileValidationResult

        with patch(
            "clarinet.services.record_service.validate_record_files",
            new_callable=AsyncMock,
        ) as mock_validate:
            mock_validate.return_value = FileValidationResult(
                valid=True, matched_files={"master_model": "master_model.seg.nrrd"}
            )
            resp = await client.post(f"{RECORDS_BASE}/{proj['id']}/check-files")
            assert resp.status_code == 200

        proj_after = await _get_record(client, proj["id"])
        assert proj_after["status"] == "pending"

    async def test_unblock_triggers_pending_flow(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        file_record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Unblocking a record triggers its on_status('pending') flow."""
        proj = await _create_record(
            client,
            "create-master-projection",
            hierarchy["patient_id"],
            hierarchy["study_uid"],
            hierarchy["series_uid"],
        )
        assert proj["status"] == "blocked"
        captured_pipelines.clear()

        # WORKAROUND: mock validate_record_files — same reason as test_unblock_via_check_files
        from clarinet.services.file_validation import FileValidationResult

        with patch(
            "clarinet.services.record_service.validate_record_files",
            new_callable=AsyncMock,
        ) as mock_validate:
            mock_validate.return_value = FileValidationResult(
                valid=True, matched_files={"master_model": "master_model.seg.nrrd"}
            )
            await client.post(f"{RECORDS_BASE}/{proj['id']}/check-files")

        # on_status("pending") should dispatch auto_project pipeline
        assert any("_dummy_auto_project" in name for name, _ in captured_pipelines)


# ============================================================================
# Tests: Full Happy Path
# ============================================================================


class TestFullCTHappyPath:
    """End-to-end CT workflow: first-check through comparison results."""

    async def test_ct_workflow_first_check_to_comparison(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """Walk through the full CT workflow and verify all records created."""
        h = hierarchy

        # Step 1: First check (good study)
        fc = await _create_record(client, "first-check", h["patient_id"], h["study_uid"])
        await _submit_data(
            client,
            fc["id"],
            {"is_good": True, "study_type": "CT", "best_series": h["series_uid"]},
        )

        # Step 2: Anonymize-study auto-created and pipeline dispatched
        anon = await _find_records(
            client, record_type_name="anonymize-study", study_uid=h["study_uid"]
        )
        assert len(anon) == 1

        # Step 3: Simulate anonymization completion
        await _submit_data(client, anon[0]["id"], {"study_type": "CT"})

        # Step 4: Verify CT segmentations created
        ct_single = await _find_records(
            client, record_type_name="segment-ct-single", study_uid=h["study_uid"]
        )
        ct_archive = await _find_records(
            client,
            record_type_name="segment-ct-with-archive",
            study_uid=h["study_uid"],
        )
        assert len(ct_single) == 1
        assert len(ct_archive) == 1

        # Step 5: Finish ct-single (simulating Slicer segmentation via image service)
        await _submit_data(client, ct_single[0]["id"], {})

        # Step 6: Verify projection + comparison created
        projections = await _find_records(
            client,
            record_type_name="create-master-projection",
            series_uid=h["series_uid"],
        )
        comparisons = await _find_records(
            client,
            record_type_name="compare-with-projection",
            series_uid=h["series_uid"],
        )
        assert len(projections) >= 1
        assert len(comparisons) >= 1

        # Step 7: Finish projection (simulating auto_project_ct)
        await _submit_data(client, projections[0]["id"], {})

        # Step 8: Submit comparison with false negatives (simulating compare_w_projection)
        await _submit_data(
            client,
            comparisons[0]["id"],
            {
                "false_negative": [{"lesion_num": 1}],
                "false_negative_num": 1,
                "false_positive_num": 0,
            },
        )

        # Step 9: Verify second-review created
        reviews = await _find_records(
            client, record_type_name="second-review", series_uid=h["series_uid"]
        )
        assert len(reviews) == 1

        # Verify pipeline dispatch count covers all expected tasks
        pipeline_names = [name for name, _ in captured_pipelines]
        assert any("_dummy_anonymize" in n for n in pipeline_names)
        assert any("_dummy_auto_project" in n for n in pipeline_names)
        assert any("_dummy_compare" in n for n in pipeline_names)

    async def test_ct_workflow_false_positive_branch(
        self,
        client: AsyncClient,
        hierarchy: dict[str, str],
        record_types: dict[str, RecordType],
        app_with_engine: RecordFlowEngine,
        captured_pipelines: list,
    ):
        """CT workflow with false positives -> update-master-model."""
        h = hierarchy

        fc = await _create_record(client, "first-check", h["patient_id"], h["study_uid"])
        await _submit_data(
            client,
            fc["id"],
            {"is_good": True, "study_type": "CT", "best_series": h["series_uid"]},
        )

        anon = await _find_records(
            client, record_type_name="anonymize-study", study_uid=h["study_uid"]
        )
        await _submit_data(client, anon[0]["id"], {"study_type": "CT"})

        ct_single = await _find_records(
            client, record_type_name="segment-ct-single", study_uid=h["study_uid"]
        )
        await _submit_data(client, ct_single[0]["id"], {})

        projections = await _find_records(
            client,
            record_type_name="create-master-projection",
            series_uid=h["series_uid"],
        )
        await _submit_data(client, projections[0]["id"], {})

        comparisons = await _find_records(
            client,
            record_type_name="compare-with-projection",
            series_uid=h["series_uid"],
        )
        await _submit_data(
            client,
            comparisons[0]["id"],
            {
                "false_negative": [],
                "false_negative_num": 0,
                "false_positive_num": 3,
            },
        )

        updates = await _find_records(
            client,
            record_type_name="update-master-model",
            patient_id=h["patient_id"],
        )
        assert len(updates) == 1

        # No second-review (no false negatives)
        reviews = await _find_records(
            client, record_type_name="second-review", series_uid=h["series_uid"]
        )
        assert len(reviews) == 0
