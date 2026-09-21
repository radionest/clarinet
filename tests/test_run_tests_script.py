"""scripts/run_tests.sh must not report an aborted or erroring pytest run as green.

The wrapper forgives a non-zero exit when xdist workers are SIGKILLed (137) at
teardown after every test passed. It once forgave *any* exit code and looked
only at ``summary.failed``, so a session that died of INTERNALERROR after 107 of
3,700 tests, or one whose fixtures all errored, still ended "All stages passed!".
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="run_tests.sh needs bash and jq",
)


def _run(tmp_path: Path, *, pytest_exit: int, summary: dict[str, int] | None) -> int:
    """Run the wrapper with a stub ``uv`` that writes ``summary`` and exits ``pytest_exit``.

    ``summary=None`` models a pytest killed before session end: no report is written.
    """
    report = tmp_path / "report.json"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    write_report = (
        f"cat > '{report}' <<'JSON'\n{json.dumps({'summary': summary})}\nJSON\n"
        if summary is not None
        else ""
    )
    fake_uv.write_text(f"#!/usr/bin/env bash\n{write_report}exit {pytest_exit}\n")
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CLARINET_TEST_REPORT": str(report),
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, check=False
    ).returncode


def test_clean_run_passes(tmp_path: Path) -> None:
    assert _run(tmp_path, pytest_exit=0, summary={"passed": 10, "total": 10}) == 0


def test_sigkill_at_teardown_after_a_clean_run_is_forgiven(tmp_path: Path) -> None:
    assert _run(tmp_path, pytest_exit=137, summary={"passed": 10, "total": 10}) == 0


def test_failures_keep_the_exit_code(tmp_path: Path) -> None:
    assert _run(tmp_path, pytest_exit=1, summary={"passed": 9, "failed": 1, "total": 10}) == 1


def test_internal_error_is_not_forgiven(tmp_path: Path) -> None:
    """Exit 3 = the session aborted; the tests that never ran are not in the report."""
    assert _run(tmp_path, pytest_exit=3, summary={"passed": 104, "skipped": 3, "total": 107}) == 3


def test_fixture_errors_are_not_forgiven(tmp_path: Path) -> None:
    """Setup/teardown errors land in ``summary.error``, not ``summary.failed``."""
    assert _run(tmp_path, pytest_exit=1, summary={"passed": 9, "error": 1, "total": 10}) == 1


def test_a_previous_stages_green_report_is_not_trusted(tmp_path: Path) -> None:
    """Every stage writes the same report path; a run killed before writing must not inherit it."""
    (tmp_path / "report.json").write_text(json.dumps({"summary": {"passed": 10, "total": 10}}))
    assert _run(tmp_path, pytest_exit=137, summary=None) == 137


def test_sigkill_does_not_hide_fixture_errors(tmp_path: Path) -> None:
    assert _run(tmp_path, pytest_exit=137, summary={"passed": 9, "error": 1, "total": 10}) == 137
