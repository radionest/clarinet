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
        base_path="/liver_nir",
    )[0]["configuration"]


def test_builtin_roots_and_conservative_flags():
    cfg = _cfg("builtin", None)
    assert cfg["qidoRoot"] == "/liver_nir/dicom-web"
    assert cfg["wadoRoot"] == "/liver_nir/dicom-web"
    assert cfg["wadoUriRoot"] == "/liver_nir/dicom-web"
    assert cfg["qidoSupportsIncludeField"] is False
    assert cfg["supportsFuzzyMatching"] is False
    assert cfg["supportsWildcard"] is False


def test_external_roots_and_fast_flags():
    cfg = _cfg("external", "/pacs-web")
    assert cfg["qidoRoot"] == "/liver_nir/pacs-web"
    assert cfg["wadoRoot"] == "/liver_nir/pacs-web"
    assert cfg["wadoUriRoot"] == "/liver_nir/pacs-web"
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
        base_path="/liver_nir/",
    )[0]["configuration"]
    assert cfg["qidoRoot"] == "/liver_nir/dicom-web"
