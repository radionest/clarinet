import pytest

from clarinet.api.ohif_config import (
    DATASOURCES_SENTINEL,
    build_datasources,
    inject_datasources,
)


def _cfg(backend, external_root):
    return build_datasources(
        backend=backend,
        external_root=external_root,
        friendly_name="Clarinet PACS",
        qido_include=None,
        fuzzy=None,
        wildcard=None,
        base_path="/demo_ndt",
    )[0]["configuration"]


def test_builtin_roots_and_conservative_flags():
    cfg = _cfg("builtin", None)
    assert cfg["qidoRoot"] == "/demo_ndt/dicom-web"
    assert cfg["wadoRoot"] == "/demo_ndt/dicom-web"
    assert cfg["wadoUriRoot"] == "/demo_ndt/dicom-web"
    assert cfg["qidoSupportsIncludeField"] is False
    assert cfg["supportsFuzzyMatching"] is False
    assert cfg["supportsWildcard"] is False


def test_external_roots_and_fast_flags():
    cfg = _cfg("external", "/pacs-web")
    assert cfg["qidoRoot"] == "/demo_ndt/pacs-web"
    assert cfg["wadoRoot"] == "/demo_ndt/pacs-web"
    assert cfg["wadoUriRoot"] == "/demo_ndt/pacs-web"
    assert cfg["qidoSupportsIncludeField"] is True
    assert cfg["supportsFuzzyMatching"] is True
    assert cfg["supportsWildcard"] is True


def test_inject_replaces_sentinel():
    text = f"window.config = {{ dataSources: {DATASOURCES_SENTINEL}, x: 1 }};"
    out = inject_datasources(text, "[1,2,3]")
    assert out is not None
    assert DATASOURCES_SENTINEL not in out
    assert "[1,2,3]" in out


def test_inject_absent_sentinel_returns_none():
    assert inject_datasources("no sentinel", "[]") is None


def test_external_without_root_raises():
    with pytest.raises(ValueError, match="external_root"):
        build_datasources(
            backend="external",
            external_root=None,
            friendly_name="x",
            qido_include=None,
            fuzzy=None,
            wildcard=None,
            base_path="",
        )


def test_trailing_slash_base_path_normalized():
    cfg = build_datasources(
        backend="builtin",
        external_root=None,
        friendly_name="x",
        qido_include=None,
        fuzzy=None,
        wildcard=None,
        base_path="/demo_ndt/",
    )[0]["configuration"]
    assert cfg["qidoRoot"] == "/demo_ndt/dicom-web"


def test_serve_app_config_renders_external(tmp_path, monkeypatch):
    # NOTE: do NOT use `with TestClient(...)` — that runs the app lifespan
    # (DB init etc.). serve_spa only needs settings + files, no lifespan.
    from fastapi.testclient import TestClient

    from clarinet.api.app import create_app
    from clarinet.settings import settings

    ohif = tmp_path / "ohif"
    ohif.mkdir()
    (ohif / "app-config.js").write_text(
        "window.config = { dataSources: __CLARINET_DATASOURCES__, "
        "defaultDataSourceName: 'dicomweb' };",
        encoding="utf-8",
    )
    monkeypatch.setattr(type(settings), "ohif_path", property(lambda _self: ohif))
    monkeypatch.setattr(settings, "ohif_enabled", True)
    monkeypatch.setattr(settings, "dicomweb_backend", "external")
    monkeypatch.setattr(settings, "dicomweb_external_root", "/pacs-web")

    client = TestClient(create_app(root_path=""))
    resp = client.get("/ohif/app-config.js")
    assert resp.status_code == 200
    assert "/pacs-web" in resp.text
    assert "__CLARINET_DATASOURCES__" not in resp.text
    assert resp.headers["content-type"].startswith("application/javascript")


def test_serve_app_config_absent_sentinel_not_cached(tmp_path, monkeypatch):
    # No `with` block — avoid running the app lifespan.
    from fastapi.testclient import TestClient

    from clarinet.api.app import create_app
    from clarinet.settings import settings

    ohif = tmp_path / "ohif"
    ohif.mkdir()
    cfg = ohif / "app-config.js"
    cfg.write_text("window.config = { no_sentinel: true };", encoding="utf-8")
    monkeypatch.setattr(type(settings), "ohif_path", property(lambda _self: ohif))
    monkeypatch.setattr(settings, "ohif_enabled", True)
    monkeypatch.setattr(settings, "dicomweb_backend", "builtin")

    client = TestClient(create_app(root_path=""))
    r1 = client.get("/ohif/app-config.js")
    assert r1.status_code == 200
    assert "no_sentinel" in r1.text  # served unrendered
    # Not cached: a later file change (e.g. after --force-config) is reflected.
    cfg.write_text("window.config = { dataSources: __CLARINET_DATASOURCES__ };", encoding="utf-8")
    r2 = client.get("/ohif/app-config.js")
    assert "__CLARINET_DATASOURCES__" not in r2.text  # self-healed, now rendered


def test_packaged_template_has_prefetcher_and_sentinel():
    """The shipped OHIF template must keep both the studyPrefetcher block and
    the dataSources sentinel. Dropping the prefetcher silently regresses
    large-study loading back to lazy per-series; dropping the sentinel breaks
    dataSources injection in serve_spa. Reads the source template directly
    (not the installed/served copy), so it guards the packaged file itself."""
    from pathlib import Path

    template = Path(__file__).resolve().parent.parent / "clarinet" / "ohif" / "app-config.js"
    text = template.read_text(encoding="utf-8")

    assert "studyPrefetcher" in text
    assert "maxNumPrefetchRequests" in text
    assert DATASOURCES_SENTINEL in text
