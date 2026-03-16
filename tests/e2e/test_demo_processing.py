"""E2E tests: full demo research processing cycle.

Loads actual RecordType definitions from ``examples/demo/tasks/``
(TOML preferred, JSON fallback) and RecordFlow rules from
``examples/demo/record_flow.py``, then exercises the complete processing
chain through the API.
"""

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.api.app import app
from clarinet.client import ClarinetClient
from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinition, FileRole, RecordTypeFileLink
from clarinet.models.patient import Patient
from clarinet.models.record import RecordType
from clarinet.models.study import Series, Study
from clarinet.models.user import UserRole
from clarinet.services.recordflow import RecordFlowEngine
from clarinet.services.recordflow.flow_file import FILE_REGISTRY
from clarinet.services.recordflow.flow_loader import load_flows_from_file
from clarinet.services.recordflow.flow_record import ENTITY_REGISTRY, RECORD_REGISTRY
from clarinet.utils.config_loader import discover_config_files, load_record_config
from clarinet.utils.file_registry_resolver import FileRegistryEntry, resolve_task_files

DEMO_DIR = Path(__file__).resolve().parent.parent.parent / "examples" / "demo"
TASKS_DIR = DEMO_DIR / "tasks"
FLOW_FILE = DEMO_DIR / "record_flow.py"


# ---------------------------------------------------------------------------
# Autouse fixtures + client override
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global FlowRecord registries between tests."""
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Override e2e conftest's unauthenticated client with an authenticated one.

    The e2e conftest yields ``unauthenticated_client`` as ``client``.
    Demo processing tests need auth bypassed, so we re-use the shared
    ``create_authenticated_client`` helper from root conftest.
    """
    from tests.conftest import create_authenticated_client, create_mock_superuser

    mock_user = await create_mock_superuser(test_session, email="e2e_demo@test.com")
    async for ac in create_authenticated_client(mock_user, test_session, test_settings):
        yield ac


# ---------------------------------------------------------------------------
# Demo data fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def demo_roles(test_session: AsyncSession) -> dict[str, UserRole]:
    """Create UserRole rows required by demo RecordTypes."""
    roles: dict[str, UserRole] = {}
    for name in ("doctor", "auto", "expert"):
        role = UserRole(name=name)
        test_session.add(role)
        roles[name] = role
    await test_session.commit()
    for role in roles.values():
        await test_session.refresh(role)
    return roles


@pytest_asyncio.fixture
async def demo_record_types(
    test_session: AsyncSession,
    demo_roles: dict[str, UserRole],
) -> dict[str, RecordType]:
    """Load all 6 RecordType definitions from the demo tasks directory.

    Uses ``config_loader`` to discover TOML/JSON configs, resolve file
    references and schemas, then inserts via direct ORM to match the
    pattern used by other integration tests.
    """
    config_files = discover_config_files(str(TASKS_DIR))

    # Load project file registry for resolving file references
    registry_path = TASKS_DIR / "file_registry.json"
    if registry_path.exists():
        raw_registry = json.loads(registry_path.read_text())
        project_registry = {
            name: FileRegistryEntry(**entry) for name, entry in raw_registry.items()
        }
    else:
        project_registry = None

    types: dict[str, RecordType] = {}
    file_def_cache: dict[str, FileDefinition] = {}

    for config_path in config_files:
        definition = await load_record_config(config_path)
        if definition is None:
            continue

        # Resolve file references against project registry
        definition = resolve_task_files(definition, project_registry)

        # Extract file_registry before creating ORM object
        file_registry_data = definition.pop("file_registry", None)

        rt = RecordType(**definition)
        rt.file_links = []
        test_session.add(rt)
        await test_session.flush()

        # Create file links
        if file_registry_data:
            for fd_data in file_registry_data:
                fd_dict = fd_data if isinstance(fd_data, dict) else fd_data.model_dump()
                name = fd_dict["name"]
                if name not in file_def_cache:
                    fd = FileDefinition(
                        name=name,
                        pattern=fd_dict.get("pattern", ""),
                        description=fd_dict.get("description"),
                        multiple=fd_dict.get("multiple", False),
                    )
                    test_session.add(fd)
                    await test_session.flush()
                    file_def_cache[name] = fd
                else:
                    fd = file_def_cache[name]

                link = RecordTypeFileLink(
                    record_type_name=rt.name,
                    file_definition_id=fd.id,  # type: ignore[arg-type]
                    role=fd_dict.get("role", FileRole.OUTPUT),
                    required=fd_dict.get("required", True),
                )
                test_session.add(link)

        types[rt.name] = rt

    await test_session.commit()

    # Expire cached state so selectinload can fetch fresh data
    test_session.expire_all()

    # Re-fetch with eager loading to populate file_links
    stmt = select(RecordType).options(
        selectinload(RecordType.file_links).selectinload(  # type: ignore[arg-type]
            RecordTypeFileLink.file_definition
        ),
    )
    result = await test_session.execute(stmt)
    for rt in result.scalars().all():
        if rt.name in types:
            types[rt.name] = rt
    return types


@pytest_asyncio.fixture
async def demo_engine(
    clarinet_client: ClarinetClient,
) -> AsyncGenerator[RecordFlowEngine]:
    """Load flows from demo record_flow.py and install engine into app.state."""
    flows = load_flows_from_file(FLOW_FILE)
    engine = RecordFlowEngine(clarinet_client)
    for flow in flows:
        engine.register_flow(flow)

    app.state.recordflow_engine = engine
    yield engine
    app.state.recordflow_engine = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_hierarchy(
    session: AsyncSession,
    engine: RecordFlowEngine | None = None,
) -> dict:
    """Create patient -> study -> series via ORM. Returns dict with IDs.

    All entities are created via ORM to avoid the MissingGreenlet error
    that occurs when ``POST /api/series`` accesses ``result.study.patient_id``
    (lazy load after ``session.refresh``). If *engine* is provided, triggers
    ``handle_entity_created`` for the series so that entity flows fire.
    """
    patient_id = f"E2E_PAT_{uuid4().hex[:8]}"
    study_uid = f"1.2.826.0.1.{uuid4().int % 10**10}"
    series_uid = f"{study_uid}.1"

    patient = Patient(id=patient_id, name="E2E Patient")
    session.add(patient)
    await session.commit()

    study = Study(
        study_uid=study_uid,
        patient_id=patient_id,
        date=datetime.now(UTC).date(),
    )
    session.add(study)
    await session.commit()

    series = Series(
        series_uid=series_uid,
        series_number=1,
        study_uid=study_uid,
    )
    session.add(series)
    await session.commit()

    if engine:
        await engine.handle_entity_created(
            "series",
            patient_id=patient_id,
            study_uid=study_uid,
            series_uid=series_uid,
        )

    return {
        "patient_id": patient_id,
        "study_uid": study_uid,
        "series_uid": series_uid,
    }


async def _add_series(
    session: AsyncSession,
    study_uid: str,
    patient_id: str,
    series_number: int,
    engine: RecordFlowEngine | None = None,
) -> str:
    """Add another series to an existing study via ORM. Returns series_uid."""
    series_uid = f"{study_uid}.{series_number}"

    series = Series(
        series_uid=series_uid,
        series_number=series_number,
        study_uid=study_uid,
    )
    session.add(series)
    await session.commit()

    if engine:
        await engine.handle_entity_created(
            "series",
            patient_id=patient_id,
            study_uid=study_uid,
            series_uid=series_uid,
        )

    return series_uid


async def _find_records(
    client: AsyncClient,
    record_type_name: str,
    study_uid: str | None = None,
    series_uid: str | None = None,
) -> list[dict]:
    """Find records of a given type via the API."""
    params: dict = {"record_type_name": record_type_name}
    if study_uid:
        params["study_uid"] = study_uid
    if series_uid:
        params["series_uid"] = series_uid

    resp = await client.post("/api/records/find", params=params)
    assert resp.status_code == 200, resp.text
    result: list[dict] = resp.json()
    return result


# ---------------------------------------------------------------------------
# Test: record types loaded
# ---------------------------------------------------------------------------


class TestDemoRecordTypes:
    """Verify demo record types are loaded correctly."""

    @pytest.mark.asyncio
    async def test_record_types_loaded_correctly(
        self,
        demo_record_types: dict[str, RecordType],
    ):
        """All 6 demo record types are loaded with correct metadata."""
        assert len(demo_record_types) == 6

        # doctor_review
        dr = demo_record_types["doctor-review"]
        assert dr.level == DicomQueryLevel.SERIES
        assert dr.role_name == "doctor"
        assert dr.data_schema is not None
        assert "diagnosis" in dr.data_schema.get("properties", {})

        # ai_analysis
        ai = demo_record_types["ai-analysis"]
        assert ai.level == DicomQueryLevel.SERIES
        assert ai.role_name == "auto"
        assert ai.data_schema is not None
        assert "ai_diagnosis" in ai.data_schema.get("properties", {})

        # expert_check
        ec = demo_record_types["expert-check"]
        assert ec.level == DicomQueryLevel.SERIES
        assert ec.role_name == "expert"
        assert ec.data_schema is not None
        assert "final_diagnosis" in ec.data_schema.get("properties", {})

        # series_markup
        sm = demo_record_types["series-markup"]
        assert sm.level == DicomQueryLevel.SERIES
        assert sm.role_name == "doctor"
        assert sm.slicer_script is not None

        # lesion_seg
        ls = demo_record_types["lesion-seg"]
        assert ls.level == DicomQueryLevel.SERIES
        assert ls.role_name == "doctor"
        ls_files = ls.file_registry
        assert ls_files is not None
        assert len(ls_files) == 1

        # air_volume
        av = demo_record_types["air-volume"]
        assert av.level == DicomQueryLevel.SERIES
        assert av.role_name is None
        av_files = av.file_registry
        assert av_files is not None
        assert len(av_files) == 1


# ---------------------------------------------------------------------------
# Test: entity flows
# ---------------------------------------------------------------------------


class TestEntityFlows:
    """Entity creation flows (series -> auto-create series_markup)."""

    @pytest.mark.asyncio
    async def test_series_creation_triggers_series_markup(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """Creating a series auto-creates a series_markup record (Flow 4)."""
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)

        records = await _find_records(client, "series-markup", study_uid=hierarchy["study_uid"])
        assert len(records) == 1
        assert records[0]["series_uid"] == hierarchy["series_uid"]

    @pytest.mark.asyncio
    async def test_multiple_series_get_independent_markups(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """Each series gets its own series_markup record."""
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)
        series_uid_2 = await _add_series(
            test_session,
            study_uid=hierarchy["study_uid"],
            patient_id=hierarchy["patient_id"],
            series_number=2,
            engine=demo_engine,
        )

        records = await _find_records(client, "series-markup", study_uid=hierarchy["study_uid"])
        assert len(records) == 2

        series_uids = {r["series_uid"] for r in records}
        assert series_uids == {hierarchy["series_uid"], series_uid_2}


# ---------------------------------------------------------------------------
# Test: doctor_review flows
# ---------------------------------------------------------------------------


class TestDoctorReviewFlows:
    """Flows triggered by doctor_review completion."""

    @pytest.mark.asyncio
    async def test_doctor_review_finished_creates_ai_analysis(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """Finishing a doctor_review with high confidence creates ai_analysis (Flow 1)."""
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)

        # Create doctor_review
        resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-review",
                "patient_id": hierarchy["patient_id"],
                "study_uid": hierarchy["study_uid"],
                "series_uid": hierarchy["series_uid"],
            },
        )
        assert resp.status_code == 201
        record_id = resp.json()["id"]

        # Submit data (auto-sets status to finished, triggers flows)
        resp = await client.post(
            f"/api/records/{record_id}/data",
            json={"diagnosis": "Normal", "confidence": 90},
        )
        assert resp.status_code == 200

        # Flow 1: ai_analysis should be created
        ai_records = await _find_records(client, "ai-analysis", study_uid=hierarchy["study_uid"])
        assert len(ai_records) == 1

    @pytest.mark.asyncio
    async def test_doctor_review_low_confidence_creates_expert_check(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """Low confidence creates both ai_analysis AND expert_check (Flows 1+2)."""
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)

        resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-review",
                "patient_id": hierarchy["patient_id"],
                "study_uid": hierarchy["study_uid"],
                "series_uid": hierarchy["series_uid"],
            },
        )
        record_id = resp.json()["id"]

        # Submit with low confidence (50 < 70)
        resp = await client.post(
            f"/api/records/{record_id}/data",
            json={"diagnosis": "Abnormal", "confidence": 50},
        )
        assert resp.status_code == 200

        # Flow 1: ai_analysis
        ai_records = await _find_records(client, "ai-analysis", study_uid=hierarchy["study_uid"])
        assert len(ai_records) == 1

        # Flow 2: expert_check (confidence < 70)
        expert_records = await _find_records(
            client, "expert-check", study_uid=hierarchy["study_uid"]
        )
        assert len(expert_records) == 1

    @pytest.mark.asyncio
    async def test_doctor_review_high_confidence_no_expert_check(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """High confidence creates ai_analysis but NOT expert_check."""
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)

        resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-review",
                "patient_id": hierarchy["patient_id"],
                "study_uid": hierarchy["study_uid"],
                "series_uid": hierarchy["series_uid"],
            },
        )
        record_id = resp.json()["id"]

        resp = await client.post(
            f"/api/records/{record_id}/data",
            json={"diagnosis": "Normal", "confidence": 90},
        )
        assert resp.status_code == 200

        # ai_analysis created (Flow 1)
        ai_records = await _find_records(client, "ai-analysis", study_uid=hierarchy["study_uid"])
        assert len(ai_records) == 1

        # expert_check NOT created (Flow 2 condition false: confidence >= 70)
        expert_records = await _find_records(
            client, "expert-check", study_uid=hierarchy["study_uid"]
        )
        assert len(expert_records) == 0


# ---------------------------------------------------------------------------
# Test: cross-record flows
# ---------------------------------------------------------------------------


class TestCrossRecordFlows:
    """Flows comparing data across record types."""

    @pytest.mark.asyncio
    async def test_ai_disagreement_creates_expert_check(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """AI disagreement with doctor creates expert_check (Flow 3)."""
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)

        # Step 1: Create and finish doctor_review (high confidence -> no expert from Flow 2)
        resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-review",
                "patient_id": hierarchy["patient_id"],
                "study_uid": hierarchy["study_uid"],
                "series_uid": hierarchy["series_uid"],
            },
        )
        dr_id = resp.json()["id"]

        resp = await client.post(
            f"/api/records/{dr_id}/data",
            json={"diagnosis": "Normal", "confidence": 90},
        )
        assert resp.status_code == 200

        # Flow 1 creates ai_analysis
        ai_records = await _find_records(client, "ai-analysis", study_uid=hierarchy["study_uid"])
        assert len(ai_records) == 1
        ai_id = ai_records[0]["id"]

        # No expert_check yet (high confidence, no disagreement yet)
        expert_before = await _find_records(
            client, "expert-check", study_uid=hierarchy["study_uid"]
        )
        assert len(expert_before) == 0

        # Step 2: Submit ai_analysis with DIFFERENT diagnosis -> Flow 3 triggers
        resp = await client.post(
            f"/api/records/{ai_id}/data",
            json={"ai_diagnosis": "Abnormal", "ai_confidence": 0.85},
        )
        assert resp.status_code == 200

        # Flow 3: expert_check created due to diagnosis disagreement
        expert_records = await _find_records(
            client, "expert-check", study_uid=hierarchy["study_uid"]
        )
        assert len(expert_records) == 1

    @pytest.mark.asyncio
    async def test_ai_agreement_no_expert_check(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """AI agreement with doctor does NOT create expert_check from Flow 3."""
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)

        # Create and finish doctor_review
        resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-review",
                "patient_id": hierarchy["patient_id"],
                "study_uid": hierarchy["study_uid"],
                "series_uid": hierarchy["series_uid"],
            },
        )
        dr_id = resp.json()["id"]

        resp = await client.post(
            f"/api/records/{dr_id}/data",
            json={"diagnosis": "Normal", "confidence": 90},
        )
        assert resp.status_code == 200

        # ai_analysis auto-created by Flow 1
        ai_records = await _find_records(client, "ai-analysis", study_uid=hierarchy["study_uid"])
        assert len(ai_records) == 1
        ai_id = ai_records[0]["id"]

        # Submit ai_analysis with SAME diagnosis -> Flow 3 should NOT trigger
        resp = await client.post(
            f"/api/records/{ai_id}/data",
            json={"ai_diagnosis": "Normal", "ai_confidence": 0.95},
        )
        assert resp.status_code == 200

        # No expert_check from Flow 3 (diagnoses agree)
        expert_records = await _find_records(
            client, "expert-check", study_uid=hierarchy["study_uid"]
        )
        assert len(expert_records) == 0


# ---------------------------------------------------------------------------
# Test: full processing chain
# ---------------------------------------------------------------------------


class TestFullProcessingChain:
    """Complete demo processing cycle end-to-end."""

    @pytest.mark.asyncio
    async def test_complete_demo_cycle(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        demo_record_types: dict[str, RecordType],
        demo_engine: RecordFlowEngine,
    ):
        """Full chain: entities, markups, reviews, AI analysis, expert check."""
        # 1. Create patient -> study -> series (Flow 4: auto-creates series_markup)
        hierarchy = await _create_hierarchy(test_session, engine=demo_engine)
        study_uid = hierarchy["study_uid"]
        series_uid = hierarchy["series_uid"]

        # 2. Verify series_markup exists (created by entity Flow 4)
        markup_records = await _find_records(client, "series-markup", study_uid=study_uid)
        assert len(markup_records) == 1
        markup_id = markup_records[0]["id"]
        assert markup_records[0]["series_uid"] == series_uid

        # 3. Submit series_markup data (Flow 5: auto-creates lesion_seg)
        resp = await client.post(
            f"/api/records/{markup_id}/data",
            json={"markup_type": "segmentation"},
        )
        assert resp.status_code == 200

        # 4. Verify lesion_seg exists
        lesion_records = await _find_records(client, "lesion-seg", study_uid=study_uid)
        assert len(lesion_records) == 1
        assert lesion_records[0]["series_uid"] == series_uid

        # 5. Create doctor_review, submit with low confidence
        #    (Flows 1+2: auto-create ai_analysis + expert_check)
        resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-review",
                "patient_id": hierarchy["patient_id"],
                "study_uid": study_uid,
                "series_uid": series_uid,
            },
        )
        assert resp.status_code == 201
        dr_id = resp.json()["id"]

        resp = await client.post(
            f"/api/records/{dr_id}/data",
            json={"diagnosis": "Suspicious", "confidence": 40},
        )
        assert resp.status_code == 200

        # 6. Verify ai_analysis and expert_check exist
        ai_records = await _find_records(client, "ai-analysis", study_uid=study_uid)
        assert len(ai_records) == 1
        ai_id = ai_records[0]["id"]

        expert_records = await _find_records(client, "expert-check", study_uid=study_uid)
        assert len(expert_records) == 1

        # 7. Submit ai_analysis with different diagnosis
        #    Flow 3 fires (ai_diagnosis != diagnosis), but expert_check creation
        #    is rejected by max_records=1 constraint (one already exists from Flow 2)
        resp = await client.post(
            f"/api/records/{ai_id}/data",
            json={"ai_diagnosis": "Normal", "ai_confidence": 0.7},
        )
        assert resp.status_code == 200

        # Still 1 expert_check (max_records=1 prevents duplicate)
        expert_records = await _find_records(client, "expert-check", study_uid=study_uid)
        assert len(expert_records) == 1

        # 8. Submit expert_check data
        ec_id = expert_records[0]["id"]
        resp = await client.post(
            f"/api/records/{ec_id}/data",
            json={
                "final_diagnosis": "Suspicious",
                "agrees_with_doctor": True,
                "agrees_with_ai": False,
            },
        )
        assert resp.status_code == 200

        # 9. Verify all records in expected final states
        all_records_resp = await client.post("/api/records/find", params={"study_uid": study_uid})
        all_records = all_records_resp.json()

        finished_ids = {r["id"] for r in all_records if r["status"] == "finished"}
        assert dr_id in finished_ids
        assert ai_id in finished_ids
        assert ec_id in finished_ids
        assert markup_id in finished_ids

        # 10. Verify total record count for the study
        # series_markup + lesion_seg + doctor_review + ai_analysis + expert_check = 5
        assert len(all_records) == 5
