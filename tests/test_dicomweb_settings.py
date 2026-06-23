import pytest
from pydantic import ValidationError

from clarinet.settings import Settings


def _isolate(monkeypatch, tmp_path):
    """Construct Settings() in an empty CWD with no ambient DICOMweb env.

    ``model_config`` loads ``./settings*.toml`` and ``./.env`` relative to the
    working directory, so chdir-ing to an empty tmp dir keeps these tests
    hermetic — a stray project settings.toml/.env can't leak a backend value in.
    """
    monkeypatch.chdir(tmp_path)
    for var in ("CLARINET_DICOMWEB_BACKEND", "CLARINET_DICOMWEB_EXTERNAL_ROOT"):
        monkeypatch.delenv(var, raising=False)


def test_external_backend_requires_root(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("CLARINET_DICOMWEB_BACKEND", "external")
    with pytest.raises(ValidationError, match="dicomweb_external_root"):
        Settings()


def test_external_root_must_be_absolute(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("CLARINET_DICOMWEB_BACKEND", "external")
    monkeypatch.setenv("CLARINET_DICOMWEB_EXTERNAL_ROOT", "pacs-web")  # missing leading slash
    with pytest.raises(ValidationError, match="absolute path"):
        Settings()


def test_external_backend_with_root_ok(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("CLARINET_DICOMWEB_BACKEND", "external")
    monkeypatch.setenv("CLARINET_DICOMWEB_EXTERNAL_ROOT", "/pacs-web")
    assert Settings().dicomweb_external_root == "/pacs-web"


def test_default_backend_is_builtin(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert Settings().dicomweb_backend == "builtin"
