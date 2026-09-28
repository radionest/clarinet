"""G2 (#518): a pip-installed clarinet scaffolds a project whose settings and plan/ load."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_BIN = "Scripts" if os.name == "nt" else "bin"
_EXE = ".exe" if os.name == "nt" else ""
_CHECK = """
import asyncio
from pathlib import Path
from clarinet.config.python_loader import load_python_config
from clarinet.settings import Settings, settings

Settings()
items = asyncio.run(load_python_config(Path(settings.config_tasks_path)))
print(sorted(item.name for item in items))
"""


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, (
        f"{cmd} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
    )
    return result.stdout


@pytest.mark.packaging
@pytest.mark.timeout(900)
def test_wheel_install_scaffolds_a_loadable_project(built_wheel: Path, tmp_path: Path) -> None:
    # A clean environment: no project venv, no CLARINET_* from the developer's shell.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CLARINET_", "VIRTUAL_ENV", "UV_PROJECT"))
    }
    venv = tmp_path / "venv"
    _run(["uv", "venv", "--python", sys.executable, str(venv)], tmp_path, env)
    python = venv / _BIN / f"python{_EXE}"
    _run(["uv", "pip", "install", "--python", str(python), str(built_wheel)], tmp_path, env)

    outside = tmp_path / "outside"  # no source checkout anywhere up this tree
    outside.mkdir()
    cli = venv / _BIN / f"clarinet{_EXE}"
    _run([str(cli), "init", "proj"], outside, env)
    project = outside / "proj"

    assert (
        _run([str(python), "-c", _CHECK], project, env).strip()
        == "['example-segment', 'first-check']"
    )
    _run([str(cli), "--help"], project, env)
