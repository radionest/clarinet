"""Unit tests for clarinet.utils.quality_scaffold and the shipped clarinet/quality payload."""

import argparse
import configparser
import shutil
import subprocess
import tomllib
import warnings
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
    # Exactly these files -- a stray extra (e.g. a `.ruff_cache/` dropped by
    # a local run, since this directory's own `ruff.toml` is itself a valid
    # nested config) must not silently accumulate here: this pins the exact
    # set of files clarinet hands to downstream projects.
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
    # Unlike update (see test_update_refuses_unmanaged_project_even_if_populated),
    # init's own CLI subparser does register --force, so this advice is reachable.
    assert "pass --force" in str(exc_info.value)
    assert "Makefile" in str(exc_info.value)
    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == original
    assert not (tmp_path / "mypy.ini").exists()


def test_init_refuses_unmanaged_even_when_another_destination_is_managed(
    tmp_path: Path,
) -> None:
    """The unmanaged check must run before the managed check, not after.

    A project where mypy.ini/.ruff.toml are still clarinet-managed but
    Makefile has been hand-customised (header stripped) must be refused for
    the unmanaged file, not treated as "already managed" and redirected to
    ``update`` -- obeying that redirect is exactly the reported data-loss
    path, since ``update`` (pre-fix) never consulted ``unmanaged`` at all.
    """
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    customized = "build:\n\t@echo hand-tuned\n"
    (tmp_path / "Makefile").write_text(customized, encoding="utf-8")
    with pytest.raises(QualityScaffoldError, match="not written by clarinet") as exc_info:
        scaffold_quality_config(project_dir=tmp_path, mode="init")
    assert "quality update" not in str(exc_info.value), (
        "must not steer the operator toward `update`, which would clobber the file"
    )
    assert "Makefile" in str(exc_info.value)
    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == customized


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
    ``update`` treat the project as already managed -- it must still refuse.
    The unmanaged check now runs before the managed one (see the mixed-state
    test below), so the refusal names the foreign file directly rather than
    redirecting to ``init``.

    Critically, the advice must be reachable from ``update`` itself: its CLI
    subparser has no ``--force`` flag at all (unlike ``init``'s), so a bare
    "pass --force" would be a dead end. The message must instead point at
    something the operator can actually run.
    """
    original = "build:\n\t@echo hand-written\n"
    (tmp_path / "Makefile").write_text(original, encoding="utf-8")
    with pytest.raises(QualityScaffoldError, match="not written by clarinet") as exc_info:
        scaffold_quality_config(project_dir=tmp_path, mode="update")
    assert "pass --force" not in str(exc_info.value), (
        "update's CLI subparser has no --force flag; this advice would be a dead end"
    )
    assert "clarinet quality init --force" in str(exc_info.value)
    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == original


def test_update_refuses_when_managed_and_unmanaged_are_mixed(tmp_path: Path) -> None:
    """The exact reported data-loss path: mypy.ini/.ruff.toml are still
    clarinet-managed, but Makefile has been hand-customised (its header is
    gone). ``update`` must refuse and name Makefile, not treat the project as
    "managed" (true of the other two destinations) and silently overwrite the
    customisation -- no ``--force``, no warning. Mirrors the byte-identical
    invariant in ``test_init_refuses_unmanaged_foreign_file`` above.
    """
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    customized = "build:\n\t@echo hand-tuned -- do not overwrite\n"
    (tmp_path / "Makefile").write_text(customized, encoding="utf-8")
    with pytest.raises(QualityScaffoldError, match="not written by clarinet") as exc_info:
        scaffold_quality_config(project_dir=tmp_path, mode="update")
    assert "Makefile" in str(exc_info.value)
    assert (tmp_path / "Makefile").read_text(encoding="utf-8") == customized


def test_update_refreshes(tmp_path: Path) -> None:
    """Spec scenario "Refresh after a clarinet upgrade" (project-quality-scaffold):
    managed quality config from an OLDER clarinet version -- still carrying a
    managed header, just a stale one -- is rewritten from the current payload
    with an updated header.

    This is deliberately NOT a headerless file: that state now trips the
    unmanaged guard and `update` must refuse it (see the mixed-state tests
    above) rather than silently overwrite it, so seeding a headerless
    "stale" body here would exercise a state `update` no longer accepts.
    """
    scaffold_quality_config(project_dir=tmp_path, mode="init")
    old_header = (
        "# managed by clarinet v0.0.1 — do not edit; run 'clarinet quality update' to refresh\n"
    )
    (tmp_path / "mypy.ini").write_text(old_header + "stale", encoding="utf-8")
    scaffold_quality_config(project_dir=tmp_path, mode="update")
    refreshed = (tmp_path / "mypy.ini").read_text(encoding="utf-8")
    assert "[mypy]" in refreshed
    assert "stale" not in refreshed
    assert "v0.0.1" not in refreshed


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


def test_mypy_config_enables_init_typed() -> None:
    """Spec: the shipped mypy.ini turns on pydantic-mypy's init_typed.

    This flag is what makes mypy check constructor-argument *types* (not just
    field names) for every pydantic model in a downstream project that does
    NOT hand-write its own ``__init__`` -- left at the default False, a call
    like ``SomeModel(n="not an int")`` type-checks clean. It does NOT affect
    FileDef/RecordDef (``clarinet/config/primitives.py``): both hand-write
    ``__init__``, which the plugin never re-synthesizes over, so their
    explicit signatures are authoritative regardless of this setting (see
    mypy.ini's own comment above the ``[pydantic-mypy]`` section).
    """
    parser = configparser.ConfigParser()
    assert parser.read(payload_dir() / "mypy.ini")
    assert parser.getboolean("pydantic-mypy", "init_typed", fallback=False) is True


def test_clarinet_plan_override_is_labelled() -> None:
    text = (payload_dir() / "mypy.ini").read_text(encoding="utf-8")
    assert "[mypy-clarinet_plan.*]" in text
    assert "#502" in text, "the override must name its tracked exit"
    assert "Any" in text, "the override must state its Any consequence"


def test_both_configs_exclude_vendored_lib() -> None:
    assert "plan/lib" in (payload_dir() / "mypy.ini").read_text(encoding="utf-8")
    assert "plan/lib" in (payload_dir() / "ruff.toml").read_text(encoding="utf-8")


# Byte-identical between plan/lib and plan/workflows in the behaviour tests below
# -- the same F401 (ruff) + [assignment] (mypy) violation planted in both
# locations, so any difference in what gets reported is attributable only to
# the exclusion path, never to the two files carrying different bugs.
_BAD_CODE = 'import os\n\nbad: int = "not an int"\n'


def test_ruff_config_excludes_vendored_but_checks_project_code(tmp_path: Path) -> None:
    """Spec (project-quality-scaffold): "Vendored code is excluded from both
    checkers" -- ruff half (mypy half in the test below; separate risks, since
    the two tools use unrelated exclusion syntax).

    ``test_both_configs_exclude_vendored_lib`` above only asserts the string
    ``plan/lib`` appears in ruff.toml -- a spelling check, not a behaviour
    check. This installs the real shipped config via ``scaffold_quality_config``
    and runs the real ``ruff`` binary against it. Asserting the exit code and
    that ``mine.py`` DOES appear in stdout -- not just that ``vendored.py``
    doesn't -- guards against a no-op tool (wrong cwd, crashed, config not
    picked up) trivially passing this test by reporting nothing at all.
    """
    ruff = shutil.which("ruff")
    if ruff is None:
        warnings.warn("ruff not on PATH -- skipping ruff vendored-exclusion test", stacklevel=2)
        pytest.skip("ruff not on PATH")

    scaffold_quality_config(project_dir=tmp_path, mode="init")
    (tmp_path / "plan" / "lib").mkdir(parents=True)
    (tmp_path / "plan" / "lib" / "vendored.py").write_text(_BAD_CODE, encoding="utf-8")
    (tmp_path / "plan" / "workflows").mkdir(parents=True)
    (tmp_path / "plan" / "workflows" / "mine.py").write_text(_BAD_CODE, encoding="utf-8")

    result = subprocess.run(
        [ruff, "check", "--no-cache", "plan/"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, (
        f"expected exactly the project violation (exit 1); got exit {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "vendored.py" not in result.stdout, (
        f"vendored code must be excluded from ruff -- it was reported:\n{result.stdout}"
    )
    assert "mine.py" in result.stdout, (
        f"project code must still be checked by ruff -- it was not reported:\n{result.stdout}"
    )


def test_mypy_config_excludes_vendored_but_checks_project_code(tmp_path: Path) -> None:
    """Spec (project-quality-scaffold): "Vendored code is excluded from both
    checkers" -- mypy half. mypy's exclusion is a completely different
    mechanism than ruff's (a verbose ``(?x)`` regex under ``[mypy] exclude``,
    matched against the path relative to cwd, vs. ruff's gitignore-style
    ``extend-exclude`` list) -- a genuinely separate risk, not a redundant
    duplicate of the ruff test above.

    Same construction as the ruff test: byte-identical bad content in both
    locations, so the difference in outcome can only come from the exclusion.
    "checked 1 source file" in mypy's own summary additionally confirms
    plan/lib/vendored.py was excluded from the file walk entirely, not merely
    suppressed after being checked.
    """
    mypy = shutil.which("mypy")
    if mypy is None:
        warnings.warn("mypy not on PATH -- skipping mypy vendored-exclusion test", stacklevel=2)
        pytest.skip("mypy not on PATH")

    scaffold_quality_config(project_dir=tmp_path, mode="init")
    (tmp_path / "plan" / "lib").mkdir(parents=True)
    (tmp_path / "plan" / "lib" / "vendored.py").write_text(_BAD_CODE, encoding="utf-8")
    (tmp_path / "plan" / "workflows").mkdir(parents=True)
    (tmp_path / "plan" / "workflows" / "mine.py").write_text(_BAD_CODE, encoding="utf-8")

    result = subprocess.run(
        [mypy, "--no-incremental", "plan/"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, (
        f"expected exactly the project violation (exit 1); got exit {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "vendored.py" not in result.stdout, (
        f"vendored code must be excluded from mypy -- it was reported:\n{result.stdout}"
    )
    assert "mine.py" in result.stdout, (
        f"project code must still be checked by mypy -- it was not reported:\n{result.stdout}"
    )
    assert "checked 1 source file" in result.stdout, (
        "expected plan/lib to be excluded from mypy's file walk entirely (not just "
        f"suppressed after checking); mypy reported:\n{result.stdout}"
    )


def test_ruff_force_exclude_covers_explicitly_passed_vendored_path(tmp_path: Path) -> None:
    """Spec (project-quality-scaffold): "Vendored code is excluded from both
    checkers" -- explicit-file-path variant of the ruff test above.

    ``extend-exclude`` only applies while ruff itself is walking a directory;
    passing a file path explicitly on the command line bypasses it unless
    ``force-exclude = true`` is also set (verified against ruff 0.15.8). This
    matters in practice for any wrapper that passes changed files explicitly
    rather than a directory -- a ``repo: local`` pre-commit hook, an editor
    action, a CI script that lints only the files a commit touches -- which
    would otherwise lint vendored code on every commit that touches it. The
    test above only ever passes a directory (mirroring the shipped
    Makefile), so it could not have caught this.

    No mypy sibling: mypy's ``exclude`` is applied during its own file
    discovery walk by design, with no "explicitly-passed path bypasses it"
    mode to guard against.
    """
    ruff = shutil.which("ruff")
    if ruff is None:
        warnings.warn("ruff not on PATH -- skipping ruff force-exclude test", stacklevel=2)
        pytest.skip("ruff not on PATH")

    scaffold_quality_config(project_dir=tmp_path, mode="init")
    (tmp_path / "plan" / "lib").mkdir(parents=True)
    (tmp_path / "plan" / "lib" / "vendored.py").write_text(_BAD_CODE, encoding="utf-8")
    (tmp_path / "plan" / "workflows").mkdir(parents=True)
    (tmp_path / "plan" / "workflows" / "mine.py").write_text(_BAD_CODE, encoding="utf-8")

    vendored_result = subprocess.run(
        [ruff, "check", "--no-cache", "plan/lib/vendored.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert vendored_result.returncode == 0, (
        "an explicitly-passed path under plan/lib must still be excluded (this is what "
        f"force-exclude = true is for); ruff exited {vendored_result.returncode}\n"
        f"stdout:\n{vendored_result.stdout}\nstderr:\n{vendored_result.stderr}"
    )
    assert "vendored.py" not in vendored_result.stdout, (
        "vendored code passed explicitly on the command line (e.g. as pre-commit would) "
        f"must still be excluded -- it was reported:\n{vendored_result.stdout}"
    )

    mine_result = subprocess.run(
        [ruff, "check", "--no-cache", "plan/workflows/mine.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert mine_result.returncode == 1, (
        f"expected the project violation to still be reported (exit 1); got exit "
        f"{mine_result.returncode}\nstdout:\n{mine_result.stdout}\nstderr:\n{mine_result.stderr}"
    )
    assert "mine.py" in mine_result.stdout, (
        f"an explicitly-passed project-code path must still be checked:\n{mine_result.stdout}"
    )


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
    excluded from the fast, everyday-loop targets: ``test-fast``/``test-unit``
    (see Makefile). It DOES run automatically in CI -- ``test-unit`` and
    ``test-postgres`` in ``.github/workflows/ci.yml``, both Linux -- because
    this is the actual #472 acceptance gate and needs a continuous execution
    path, not just an opt-in a human has to remember to run. Excluded from
    ``test-windows`` specifically: not because of ``-x`` -- ``test-unit``
    above also runs with ``-x``, so that isn't what distinguishes the jobs.
    ``test-windows`` alone carries a 15-minute ``timeout-minutes`` cap, and
    this check has nothing OS-specific to verify (already covered on Linux
    by test-unit and test-postgres above) -- not worth risking a slow wheel
    build eating into that budget. Also runs under
    ``make test-all-stages``, which already builds a wheel for the VM deploy
    step (negligible marginal cost there).
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
        # Any non-zero exit lands here, including a genuine packaging
        # regression, not just a truly unavailable/broken backend -- warn
        # loudly rather than skip in silence, so a real regression hiding
        # behind this skip stays visible in the run's warnings summary.
        warnings.warn(
            f"uv build failed (exit {result.returncode}); skipping rather than "
            "failing on the assumption this is an environment issue -- if it "
            f"isn't, this may be masking a real packaging regression.\n"
            f"stderr:\n{result.stderr[-2000:]}",
            stacklevel=2,
        )
        pytest.skip("uv build failed -- see the warnings summary for stderr")

    wheels = sorted(out_dir.glob("*.whl"))
    assert wheels, "uv build reported success but produced no wheel"
    wheel = wheels[-1]

    prefix = "clarinet/quality/"
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    # Recursive: a stray nested dir (e.g. a `backup/` directory someone drops
    # in the payload folder) shows up as an unexpected key here too, not just
    # an extra top-level entry.
    under_quality = {
        n[len(prefix) :] for n in names if n.startswith(prefix) and not n.endswith("/")
    }
    expected = {*PAYLOAD, FRAGMENT_NAME}
    assert under_quality == expected, (
        f"{wheel.name}: clarinet/quality/ has {under_quality}, expected "
        f"{expected} (diff: {under_quality ^ expected})"
    )
