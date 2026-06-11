"""Unit tests for ``clarinet quarto`` CLI subcommands."""

import contextlib
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from clarinet.cli.main import (
    _download_file,
    _quarto_tarball_version,
    install_quarto,
    quarto_status,
)
from clarinet.settings import settings


def _make_quarto_tarball(path: Path, top_dir: str = "quarto-1.5.57") -> Path:
    """Build a minimal Quarto-shaped tarball: ``<top_dir>/bin/quarto``."""
    src = path / "src" / top_dir
    (src / "bin").mkdir(parents=True)
    (src / "bin" / "quarto").write_text("#!/bin/sh\n")
    tarball = path / f"{top_dir}-linux-amd64.tar.gz"
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(src, arcname=top_dir)
    return tarball


def test_tarball_version_parsed_from_top_dir(tmp_path: Path) -> None:
    tarball = _make_quarto_tarball(tmp_path, top_dir="quarto-1.5.57")
    assert _quarto_tarball_version(tarball) == "1.5.57"


def test_tarball_version_none_for_unversioned_dir(tmp_path: Path) -> None:
    tarball = _make_quarto_tarball(tmp_path, top_dir="quarto")
    assert _quarto_tarball_version(tarball) is None


def test_tarball_version_none_for_corrupt_file(tmp_path: Path) -> None:
    bogus = tmp_path / "quarto-1.5.57-linux-amd64.tar.gz"
    bogus.write_bytes(b"not a tarball")
    assert _quarto_tarball_version(bogus) is None


@pytest.fixture
def quarto_install_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    install_dir = tmp_path / "quarto-install"
    monkeypatch.setattr(type(settings), "quarto_install_path", property(lambda _self: install_dir))
    monkeypatch.setattr(settings, "quarto_default_version", "9.9.9")
    return install_dir


def test_from_file_writes_tarball_version_not_default(
    quarto_install_dir: Path, tmp_path: Path
) -> None:
    tarball = _make_quarto_tarball(tmp_path, top_dir="quarto-1.5.57")
    install_quarto(from_file=str(tarball))
    marker = (quarto_install_dir / ".quarto-version").read_text().strip()
    assert marker == "1.5.57"


def test_from_file_idempotent_against_tarball_version(
    quarto_install_dir: Path, tmp_path: Path
) -> None:
    tarball = _make_quarto_tarball(tmp_path, top_dir="quarto-1.5.57")
    install_quarto(from_file=str(tarball))
    sentinel = quarto_install_dir / "sentinel"
    sentinel.write_text("keep me")
    # A repeated install of the same tarball must skip, not wipe the directory.
    install_quarto(from_file=str(tarball))
    assert sentinel.exists()


def test_explicit_version_wins_over_tarball(quarto_install_dir: Path, tmp_path: Path) -> None:
    tarball = _make_quarto_tarball(tmp_path, top_dir="quarto-1.5.57")
    install_quarto(version="2.0.0", from_file=str(tarball))
    marker = (quarto_install_dir / ".quarto-version").read_text().strip()
    assert marker == "2.0.0"


def test_from_file_unversioned_falls_back_to_default(
    quarto_install_dir: Path, tmp_path: Path
) -> None:
    tarball = _make_quarto_tarball(tmp_path, top_dir="quarto")
    install_quarto(from_file=str(tarball))
    marker = (quarto_install_dir / ".quarto-version").read_text().strip()
    assert marker == "9.9.9"


def test_download_file_sets_socket_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Guard against regressing to urlretrieve, which hangs on a dead network."""
    captured: dict[str, float | None] = {}

    @contextlib.contextmanager
    def fake_urlopen(url: str, timeout: float | None = None):
        captured["timeout"] = timeout
        yield io.BytesIO(b"payload")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    dest = tmp_path / "out.tgz"
    _download_file("https://example.org/quarto.tar.gz", dest)
    assert dest.read_bytes() == b"payload"
    assert captured["timeout"] is not None
    assert captured["timeout"] > 0


def test_quarto_check_runs_from_neutral_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Quarto's startup dotenv loader reads .env/.env.example from the CWD and
    aborts when example vars are undefined — ``quarto check`` must not inherit
    the operator's project directory (downstream projects ship populated
    ``.env.example`` files)."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env.example").write_text("CLARINET_SECRET_KEY=\n")
    monkeypatch.chdir(project)

    exe = tmp_path / "bin" / "quarto"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr("clarinet.services.quarto_render.resolve_quarto_executable", lambda: exe)

    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        cwd = kwargs.get("cwd")
        seen["cwd"] = cwd
        seen["cwd_exists"] = cwd is not None and Path(str(cwd)).is_dir()
        seen["cwd_has_env_example"] = cwd is not None and (Path(str(cwd)) / ".env.example").exists()
        return subprocess.CompletedProcess(list(cmd), 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    quarto_status()

    assert seen["cwd"], "quarto check must run with an explicit neutral cwd"
    assert Path(str(seen["cwd"])).resolve() != project.resolve()
    assert seen["cwd_exists"] is True
    assert seen["cwd_has_env_example"] is False


def test_quarto_status_uses_render_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``quarto check`` must run in the same minimal env as real renders —
    otherwise status is green in the operator's shell while renders fail."""
    monkeypatch.setenv("CLARINET_SECRET_KEY", "shh")

    exe = tmp_path / "bin" / "quarto"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr("clarinet.services.quarto_render.resolve_quarto_executable", lambda: exe)

    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(list(cmd), 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    quarto_status()

    env = seen["env"]
    assert isinstance(env, dict)
    assert env["QUARTO_PYTHON"]
    assert not any(key.startswith("CLARINET_") for key in env)
