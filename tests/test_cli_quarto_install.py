"""Unit tests for ``clarinet quarto install`` version-marker handling."""

import tarfile
from pathlib import Path

import pytest

from clarinet.cli.main import _quarto_tarball_version, install_quarto
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
