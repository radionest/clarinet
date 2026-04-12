"""Tests for viewer plugin system — registry, adapters, and API endpoint."""

import pytest

from clarinet.services.viewer import ViewerRegistry, build_viewer_registry
from clarinet.services.viewer.adapters import RadiantAdapter, TemplateAdapter, WeasisAdapter
from clarinet.services.viewer.registry import ViewerConfig

# --- Unit tests: adapters ---


class TestRadiantAdapter:
    def test_build_uri_default_pacs(self):
        adapter = RadiantAdapter()
        uri = adapter.build_uri(patient_id="P001", study_uid="1.2.3.4")
        assert uri == "radiant://?n=paet&v=ORTHANC&n=pstv&v=0020000D&v=1.2.3.4"

    def test_build_uri_custom_pacs(self):
        adapter = RadiantAdapter(pacs_name="MY_PACS")
        uri = adapter.build_uri(patient_id="P001", study_uid="1.2.3.4")
        assert "v=MY_PACS" in uri
        assert "v=1.2.3.4" in uri

    def test_from_config(self):
        config = ViewerConfig(enabled=True, pacs_name="CUSTOM")
        adapter = RadiantAdapter.from_config(config)
        assert adapter.pacs_name == "CUSTOM"

    def test_from_config_default(self):
        config = ViewerConfig(enabled=True)
        adapter = RadiantAdapter.from_config(config)
        assert adapter.pacs_name == "ORTHANC"


class TestWeasisAdapter:
    def test_build_uri_study_only(self):
        adapter = WeasisAdapter(base_url="http://pacs:8080/connector")
        uri = adapter.build_uri(patient_id="P001", study_uid="1.2.3")
        assert uri == "http://pacs:8080/connector/weasis?studyUID=1.2.3&patientID=P001"

    def test_build_uri_with_series(self):
        adapter = WeasisAdapter(base_url="http://pacs:8080/connector/")
        uri = adapter.build_uri(patient_id="P001", study_uid="1.2.3", series_uid="4.5.6")
        assert "seriesUID=4.5.6" in uri
        assert "studyUID=1.2.3" in uri

    def test_trailing_slash_stripped(self):
        adapter = WeasisAdapter(base_url="http://host/")
        assert adapter.base_url == "http://host"

    def test_from_config_missing_base_url(self):
        config = ViewerConfig(enabled=True)
        with pytest.raises(ValueError, match="base_url"):
            WeasisAdapter.from_config(config)


class TestTemplateAdapter:
    def test_build_uri(self):
        adapter = TemplateAdapter(
            name="dcm4chee",
            template="https://dcm4chee.local/studies/{study_uid}/series/{series_uid}",
        )
        uri = adapter.build_uri(patient_id="P001", study_uid="1.2.3", series_uid="4.5.6")
        assert uri == "https://dcm4chee.local/studies/1.2.3/series/4.5.6"

    def test_build_uri_no_series(self):
        adapter = TemplateAdapter(name="simple", template="viewer://{study_uid}")
        uri = adapter.build_uri(patient_id="P001", study_uid="1.2.3")
        assert uri == "viewer://1.2.3"

    def test_patient_id_in_template(self):
        adapter = TemplateAdapter(name="x", template="x://{patient_id}/{study_uid}")
        uri = adapter.build_uri(patient_id="PAT_1", study_uid="1.2.3")
        assert uri == "x://PAT_1/1.2.3"


# --- Unit tests: registry ---


class TestViewerRegistry:
    def test_register_and_get(self):
        registry = ViewerRegistry()
        adapter = RadiantAdapter(pacs_name="TEST")
        registry.register(adapter)
        assert registry.get("radiant") is adapter
        assert registry.get("nonexistent") is None

    def test_available(self):
        registry = ViewerRegistry()
        registry.register(RadiantAdapter())
        registry.register(WeasisAdapter(base_url="http://x"))
        assert set(registry.available) == {"radiant", "weasis"}

    def test_build_all_uris(self):
        registry = ViewerRegistry()
        registry.register(RadiantAdapter(pacs_name="P"))
        registry.register(WeasisAdapter(base_url="http://w"))
        uris = registry.build_all_uris(patient_id="P001", study_uid="1.2.3")
        assert "radiant" in uris
        assert "weasis" in uris
        assert "1.2.3" in uris["radiant"]
        assert "1.2.3" in uris["weasis"]

    def test_empty_registry(self):
        registry = ViewerRegistry()
        assert registry.build_all_uris(patient_id="P", study_uid="S") == {}
        assert registry.available == []


class TestBuildViewerRegistry:
    def test_build_with_builtin(self):
        configs = {"radiant": ViewerConfig(enabled=True, pacs_name="MY_PACS")}
        registry = build_viewer_registry(configs)
        assert "radiant" in registry.available

    def test_disabled_skipped(self):
        configs = {"radiant": ViewerConfig(enabled=False)}
        registry = build_viewer_registry(configs)
        assert registry.available == []

    def test_template_adapter(self):
        configs = {"custom": ViewerConfig(enabled=True, uri_template="x://{study_uid}")}
        registry = build_viewer_registry(configs)
        assert "custom" in registry.available
        adapter = registry.get("custom")
        assert adapter is not None
        uri = adapter.build_uri(patient_id="P", study_uid="1.2")
        assert uri == "x://1.2"

    def test_unknown_without_template_skipped(self):
        configs = {"unknown_viewer": ViewerConfig(enabled=True)}
        registry = build_viewer_registry(configs)
        assert registry.available == []


# --- Integration tests: API endpoints ---


@pytest.fixture
def _inject_viewer_registry(client):
    """Inject a test viewer registry into the app state."""
    registry = ViewerRegistry()
    registry.register(RadiantAdapter(pacs_name="TEST_PACS"))
    client._transport.app.state.viewer_registry = registry  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_inject_viewer_registry")
async def test_list_viewer_urls(client, test_session):
    """GET /api/records/{id}/viewers returns URIs for all enabled viewers."""
    from tests.utils.factories import make_patient, make_record_type, make_series, make_study

    rt = make_record_type("vwr-test-rt", level="SERIES")
    patient = make_patient("VWR_P1")
    study = make_study("VWR_P1", uid="1.2.3.4.5")
    series = make_series("1.2.3.4.5", uid="1.2.3.4.5.1")

    for obj in (rt, patient, study, series):
        test_session.add(obj)
    await test_session.commit()

    from tests.utils.factories import seed_record

    record = await seed_record(
        test_session,
        "VWR_P1",
        "1.2.3.4.5",
        "1.2.3.4.5.1",
        "vwr-test-rt",
    )

    resp = await client.get(f"/api/records/{record.id}/viewers")
    assert resp.status_code == 200
    data = resp.json()
    assert "radiant" in data
    assert "1.2.3.4.5" in data["radiant"]
    assert "TEST_PACS" in data["radiant"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_inject_viewer_registry")
async def test_get_specific_viewer_url(client, test_session):
    """GET /api/records/{id}/viewers/radiant returns URI for that viewer."""
    from tests.utils.factories import make_patient, make_record_type, make_series, make_study

    rt = make_record_type("vwr-rt-2", level="SERIES")
    patient = make_patient("VWR_P2")
    study = make_study("VWR_P2", uid="9.8.7.6")
    series = make_series("9.8.7.6", uid="9.8.7.6.1")

    for obj in (rt, patient, study, series):
        test_session.add(obj)
    await test_session.commit()

    from tests.utils.factories import seed_record

    record = await seed_record(
        test_session,
        "VWR_P2",
        "9.8.7.6",
        "9.8.7.6.1",
        "vwr-rt-2",
    )

    resp = await client.get(f"/api/records/{record.id}/viewers/radiant")
    assert resp.status_code == 200
    data = resp.json()
    assert data["viewer"] == "radiant"
    assert "9.8.7.6" in data["uri"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("_inject_viewer_registry")
async def test_viewer_not_found(client, test_session):
    """GET /api/records/{id}/viewers/nonexistent returns 404."""
    from tests.utils.factories import make_patient, make_record_type, make_series, make_study

    rt = make_record_type("vwr-rt-3", level="SERIES")
    patient = make_patient("VWR_P3")
    study = make_study("VWR_P3", uid="5.5.5")
    series = make_series("5.5.5", uid="5.5.5.1")

    for obj in (rt, patient, study, series):
        test_session.add(obj)
    await test_session.commit()

    from tests.utils.factories import seed_record

    record = await seed_record(
        test_session,
        "VWR_P3",
        "5.5.5",
        "5.5.5.1",
        "vwr-rt-3",
    )

    resp = await client.get(f"/api/records/{record.id}/viewers/nonexistent")
    assert resp.status_code == 404
