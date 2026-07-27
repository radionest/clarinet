"""Unit tests for clarinet.utils.quality_scaffold and the shipped clarinet/quality payload."""

import argparse
import configparser
import shutil
import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

from clarinet.cli.main import handle_quality_command
from clarinet.exceptions.domain import QualityScaffoldError
from clarinet.utils import quality_scaffold
from clarinet.utils.quality_scaffold import (
    FRAGMENT_NAME,
    PAYLOAD,
    payload_dir,
    scaffold_quality_config,
)


def test_payload_files_present() -> None:
    src = payload_dir()
    expected = {*PAYLOAD, FRAGMENT_NAME}
    for name in expected:
        assert (src / name).is_file(), f"missing payload file {name}"
    # Exactly these files -- a stray extra (e.g. a `.ruff_cache/` dropped by a
    # local run) would otherwise ship silently: Task 7's wheel packaging
    # force-includes clarinet/quality/**/*, which overrides VCS exclusions.
    actual = {p.name for p in src.iterdir()}
    assert actual == expected, f"payload dir has unexpected entries: {actual - expected}"


def test_payload_dir_is_inside_the_package() -> None:
    import clarinet

    pkg = Path(clarinet.__file__).resolve().parent
    assert pkg in payload_dir().parents or payload_dir().parent == pkg


def test_init_writes_destinations_with_header(tmp_path: Path) -> None:
    _, fragment = scaffold_quality_config(project_dir=tmp_path, mode="init")
    src = payload_dir()
    for name, dest in PAYLOAD.items():
        written = tmp_path / dest
        assert written.is_file()
        written_text = written.read_text(encoding="utf-8")
        # Strong form: the header is the FIRST line and names the clarinet
        # version (spec: "a managed header naming the clarinet version") --
        # a bare substring match would also pass if the header landed mid-file
        # (e.g. inside a Makefile recipe) or dropped the version.
        assert written_text.startswith("# managed by clarinet v")
        # Exact equality of everything after the header's own line, not just
        # "still parses" -- every payload file's first line is itself a `#`
        # comment, so dropping the header's trailing newline would merge the
        # two lines and still parse cleanly, passing a weaker check silently.
        assert written_text.split("\n", 1)[1] == (src / name).read_text(encoding="utf-8")
    assert "dependency-groups" in fragment


def test_init_writes_dotted_ruff_name(tmp_path: Path) -> None:
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    dest = PAYLOAD["ruff.toml"]
    assert dest.startswith(".")
    assert (tmp_path / dest).is_file()
    assert not (tmp_path / "ruff.toml").exists()


def test_written_configs_still_parse_after_header(tmp_path: Path) -> None:
    """Guard the header-prepend itself, not just the payload originals.

    A regression in ``header + text`` (e.g. losing the trailing newline)
    would run the header straight into the first payload line and corrupt
    every destination -- undetected by tests that only ever parse the
    original payload files.
    """
    scaffold_quality_config(project_dir=tmp_path, mode="init")

    mypy_parser = configparser.ConfigParser()
    assert mypy_parser.read(tmp_path / "mypy.ini")
    assert "mypy" in mypy_parser.sections()

    ruff_parsed = tomllib.loads((tmp_path / PAYLOAD["ruff.toml"]).read_text(encoding="utf-8"))
    assert "lint" in ruff_parsed


def test_init_never_writes_pyproject(tmp_path: Path) -> None:
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    assert not (tmp_path / "pyproject.toml").exists()
    assert not (tmp_path / FRAGMENT_NAME).exists()


def test_init_leaves_existing_pyproject_untouched(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    original = '[project]\nname = "x"\n'
    pyproject.write_text(original, encoding="utf-8")
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    assert pyproject.read_text(encoding="utf-8") == original


def test_init_refuses_existing_without_force(tmp_path: Path) -> None:
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    original = (tmp_path / "mypy.ini").read_text(encoding="utf-8")
    with pytest.raises(QualityScaffoldError, match="quality update"):
        scaffold_quality_config(project_dir=tmp_path, mode="init")
    # AC: "MUST NOT modify any file" -- the refusal must not have touched it.
    assert (tmp_path / "mypy.ini").read_text(encoding="utf-8") == original


def test_init_force_overwrites(tmp_path: Path) -> None:
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    (tmp_path / "mypy.ini").write_text("clobbered", encoding="utf-8")
    scaffold_quality_config(project_dir=tmp_path, mode="init", force=True)
    assert "clobbered" not in (tmp_path / "mypy.ini").read_text(encoding="utf-8")


def test_init_refuses_unmanaged_foreign_file(tmp_path: Path) -> None:
    """Managed status must be judged by the header, not bare existence.

    A downstream project's own hand-written ``Makefile`` must not be mistaken
    for a stale clarinet config -- and critically, the refusal must not be
    the "run update" message, since obeying that would then let ``update``
    overwrite it (the exact reported data-loss path).
    """
    original = "build:\n\t@echo hand-written\n"
    (tmp_path / "Makefile").write_text(original, encoding="utf-8")
    with pytest.raises(QualityScaffoldError, match="not written by clarinet") as exc_info:
        scaffold_quality_config(project_dir=tmp_path, mode="init")
    assert "quality update" not in str(exc_info.value), (
        "must not steer the operator toward `update`, which would clobber the file"
    )
    assert "Makefile" in str(exc_info.value)
    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == original
    assert not (tmp_path / "mypy.ini").exists()


def test_init_force_overwrites_unmanaged_foreign_file(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("build:\n\t@echo hand-written\n", encoding="utf-8")
    scaffold_quality_config(project_dir=tmp_path, mode="init", force=True)
    assert "hand-written" not in (tmp_path / "Makefile").read_text(encoding="utf-8")


def test_init_treats_undecodable_destination_as_unmanaged(tmp_path: Path) -> None:
    """A destination file that ``_is_managed`` can't even decode must not crash.

    A hand-written file in a legacy encoding (e.g. cp1251 Russian text, not
    exotic) isn't clarinet's -- treated as unmanaged, not left to raise
    ``UnicodeDecodeError`` past ``scaffold_quality_config``.
    """
    (tmp_path / "Makefile").write_bytes("# Комментарий\n".encode("cp1251"))
    with pytest.raises(QualityScaffoldError, match="not written by clarinet"):
        scaffold_quality_config(project_dir=tmp_path, mode="init")


def test_update_requires_existing(tmp_path: Path) -> None:
    with pytest.raises(QualityScaffoldError, match="quality init"):
        scaffold_quality_config(project_dir=tmp_path, mode="update")


def test_update_refuses_unmanaged_project_even_if_populated(tmp_path: Path) -> None:
    """A foreign file at a destination name must not count as populated and let
    ``update`` treat the project as already managed -- it must still refuse
    and point at ``init``.
    """
    original = "build:\n\t@echo hand-written\n"
    (tmp_path / "Makefile").write_text(original, encoding="utf-8")
    with pytest.raises(QualityScaffoldError, match="quality init"):
        scaffold_quality_config(project_dir=tmp_path, mode="update")
    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == original


def test_update_refreshes(tmp_path: Path) -> None:
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    (tmp_path / "mypy.ini").write_text("stale", encoding="utf-8")
    scaffold_quality_config(project_dir=tmp_path, mode="update")
    assert "[mypy]" in (tmp_path / "mypy.ini").read_text(encoding="utf-8")


def test_update_leaves_pyproject_byte_identical(tmp_path: Path) -> None:
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    pyproject = tmp_path / "pyproject.toml"
    original = '[project]\nname = "x"\n\n[dependency-groups]\ndev = ["ruff"]\n'
    pyproject.write_text(original, encoding="utf-8")
    scaffold_quality_config(project_dir=tmp_path, mode="update")
    assert pyproject.read_text(encoding="utf-8") == original


def test_mypy_config_is_not_strict() -> None:
    """Spec: WHEN the shipped mypy.ini is parsed THEN strict is absent or false.

    A substring match would false-fail on Task 4's "path to strict" ladder
    comment, whose item 4 names the ``strict`` flag in prose. Parsing the
    actual [mypy] section is what the spec asks for and is robust to that
    comment.
    """
    parser = configparser.ConfigParser()
    parsed = parser.read(payload_dir() / "mypy.ini")
    # ConfigParser.read() silently returns [] for a missing/unreadable file --
    # without this, a wrong path would pass vacuously via the fallback below.
    assert parsed, "mypy.ini did not parse"
    assert parser.getboolean("mypy", "strict", fallback=False) is False


def test_clarinet_plan_override_is_labelled() -> None:
    text = (payload_dir() / "mypy.ini").read_text(encoding="utf-8")
    assert "[mypy-clarinet_plan.*]" in text
    assert "#502" in text, "the override must name its tracked exit"
    assert "Any" in text, "the override must state its Any consequence"


def test_both_configs_exclude_vendored_lib() -> None:
    assert "plan/lib" in (payload_dir() / "mypy.ini").read_text(encoding="utf-8")
    assert "plan/lib" in (payload_dir() / "ruff.toml").read_text(encoding="utf-8")


def test_missing_payload_file_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No-partial-write guarantee: a payload dir missing one file must raise
    before touching ``project_dir`` at all -- it must not write the other,
    present payload files first and fail partway through.
    """
    incomplete = tmp_path / "incomplete_payload"
    incomplete.mkdir()
    for name in PAYLOAD:
        (incomplete / name).write_text("placeholder", encoding="utf-8")
    (incomplete / "ruff.toml").unlink()  # drop exactly one payload file
    (incomplete / FRAGMENT_NAME).write_text("[dependency-groups]\n", encoding="utf-8")
    monkeypatch.setattr(quality_scaffold, "payload_dir", lambda: incomplete)

    project_dir = tmp_path / "project"
    with pytest.raises(QualityScaffoldError, match=r"ruff\.toml"):
        scaffold_quality_config(project_dir=project_dir, mode="init")
    assert not project_dir.exists()


def test_cli_init_then_update(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        command="quality", quality_command="init", path=str(tmp_path), force=False
    )
    handle_quality_command(args)
    assert (tmp_path / "mypy.ini").is_file()
    # The fragment must reach stdout -- it is the required next step. Ordering
    # relative to the logger's success line can't be asserted here: loguru's
    # enqueue=True console sink holds the real sys.stderr captured at add()
    # time, bypassing capsys entirely -- this only pins the fragment's own
    # shape (exact pins present) and internal order (banner before content).
    out = capsys.readouterr().out
    assert "NEXT STEP" in out
    assert "ruff==" in out and "mypy==" in out
    assert out.index("NEXT STEP") < out.index("[dependency-groups]")

    upd = argparse.Namespace(command="quality", quality_command="update", path=str(tmp_path))
    handle_quality_command(upd)  # must not raise now that the dir is populated


def test_cli_init_existing_exits(tmp_path: Path) -> None:
    args = argparse.Namespace(
        command="quality", quality_command="init", path=str(tmp_path), force=False
    )
    handle_quality_command(args)
    with pytest.raises(SystemExit) as exc:
        handle_quality_command(args)
    assert exc.value.code == 1


@pytest.mark.packaging
@pytest.mark.timeout(300)
def test_wheel_contains_exactly_the_quality_payload(tmp_path: Path) -> None:
    """Wheel-side twin of ``test_payload_files_present`` above.

    That test can only ever see the source tree -- it cannot catch a payload
    dropped at the packaging boundary, which is exactly the shape of #472 (a
    payload present in the source tree and absent from the built wheel). This
    builds a real wheel and inspects it directly.

    Slow (spawns ``uv build``), so it carries the ``packaging`` marker and is
    excluded from ``test-fast``/``test-unit`` (see Makefile). It still runs
    under ``make test-all-stages``, which already builds a wheel for the VM
    deploy step, so the marginal cost there is negligible.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH -- cannot build a wheel")

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = tmp_path / "dist"
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(repo_root)],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        pytest.skip(f"wheel build backend unavailable: {result.stderr[-2000:]}")

    wheels = sorted(out_dir.glob("*.whl"))
    assert wheels, "uv build reported success but produced no wheel"

    prefix = "clarinet/quality/"
    names = zipfile.ZipFile(wheels[-1]).namelist()
    # Recursive: a stray nested dir (e.g. a `.ruff_cache/` dropped by a local
    # run) shows up as an unexpected key here too, not just an extra
    # top-level entry.
    under_quality = {
        n[len(prefix) :] for n in names if n.startswith(prefix) and not n.endswith("/")
    }
    expected = {*PAYLOAD, FRAGMENT_NAME}
    assert under_quality == expected, (
        f"clarinet/quality/ in the wheel has {under_quality}, expected {expected}"
    )
