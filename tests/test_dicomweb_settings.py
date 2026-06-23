import pytest
from pydantic import ValidationError

from clarinet.settings import Settings


def test_external_backend_requires_root(monkeypatch):
    monkeypatch.setenv("CLARINET_DICOMWEB_BACKEND", "external")
    monkeypatch.delenv("CLARINET_DICOMWEB_EXTERNAL_ROOT", raising=False)
    with pytest.raises(ValidationError, match="dicomweb_external_root"):
        Settings()


def test_external_backend_with_root_ok(monkeypatch):
    monkeypatch.setenv("CLARINET_DICOMWEB_BACKEND", "external")
    monkeypatch.setenv("CLARINET_DICOMWEB_EXTERNAL_ROOT", "/pacs-web")
    assert Settings().dicomweb_external_root == "/pacs-web"


def test_default_backend_is_builtin(monkeypatch):
    monkeypatch.delenv("CLARINET_DICOMWEB_BACKEND", raising=False)
    assert Settings().dicomweb_backend == "builtin"
