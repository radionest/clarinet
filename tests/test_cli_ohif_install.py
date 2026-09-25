"""Unit tests for ``clarinet ohif install``."""

import io
import tarfile
import tempfile
from pathlib import Path

import pytest

from clarinet.cli.main import install_ohif
from clarinet.settings import settings


def test_tar_slip_member_aborts_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """#604: a member escaping the extraction dir must abort, not land on disk."""
    ohif_dir = tmp_path / "ohif"
    ohif_dir.mkdir()
    (ohif_dir / "sentinel").write_text("keep me")
    monkeypatch.setattr(type(settings), "ohif_path", property(lambda _self: ohif_dir))
    # Extract under tmp_path/t so that without the filter "../escaped" stays inside tmp_path.
    (tmp_path / "t").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "t"))

    tarball = tmp_path / "ohif.tgz"
    with tarfile.open(tarball, "w:gz") as tf:
        for name in ("package/dist/index.html", "../escaped"):
            info = tarfile.TarInfo(name)
            info.size = 2
            tf.addfile(info, io.BytesIO(b"hi"))

    with pytest.raises(SystemExit) as exc:
        install_ohif(version="1.0.0", from_file=str(tarball))

    assert exc.value.code == 1
    assert not (tmp_path / "t" / "escaped").exists()
    assert (ohif_dir / "sentinel").exists()
