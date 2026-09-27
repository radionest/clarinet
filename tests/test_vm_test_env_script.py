"""scripts/vm-test-env.sh must point every test service variable at the pipeline VM.

Stage 5 and the PostgreSQL pass once forwarded the VM as PACS but not as broker,
so the pipeline tests fell back to localhost as clarinet_test and failed with
ACCESS_REFUSED on any box running its own RabbitMQ (#624).
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vm-test-env.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None or shutil.which("sh") is None,
    reason="vm-test-env.sh is a POSIX developer tool",
)

VM_IP = "192.0.2.10"


def _env(tmp_path: Path, *, login: str, password: str) -> dict[str, str]:
    """Environment with a stub ``ssh`` answering vm-setting.sh's two reads."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "login").write_text(login)
    (tmp_path / "password").write_text(password)
    fake_ssh = bin_dir / "ssh"
    # vm-setting.sh passes the setting key as the tail of the remote command.
    fake_ssh.write_text(
        "#!/usr/bin/env bash\n"
        'case "${!#}" in\n'
        f"  *rabbitmq_login) cat '{tmp_path / 'login'}' ;;\n"
        f"  *rabbitmq_password) cat '{tmp_path / 'password'}' ;;\n"
        "esac\n"
    )
    fake_ssh.chmod(fake_ssh.stat().st_mode | stat.S_IXUSR)
    # A set SSH_KEY_PATH stops vm-setting.sh from sourcing the real vm.conf.
    return {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "SSH_KEY_PATH": str(tmp_path / "key"),
    }


def _eval_in_sh(env: dict[str, str], *names: str) -> list[str]:
    """``eval`` the script's output in POSIX sh, as the Makefile recipe does."""
    printed = " ".join(f'"${{{n}-UNSET}}"' for n in names)
    probe = f'eval "$(bash "$0" {VM_IP})" && printf "%s\\n" {printed}'
    out = subprocess.run(
        ["sh", "-c", probe, str(SCRIPT)], env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def test_forces_the_vm_as_pacs_and_broker(tmp_path: Path) -> None:
    env = _env(tmp_path, login="clarinet", password="0123abcd")
    names = {
        "CLARINET_TEST_PACS_HOST": VM_IP,
        "CLARINET_TEST_PACS_SSH": "",
        "CLARINET_TEST_RABBITMQ_HOST": VM_IP,
        "CLARINET_TEST_RABBITMQ_PORT": "5672",
        "CLARINET_TEST_RABBITMQ_MANAGEMENT_PORT": "15672",
        "CLARINET_TEST_RABBITMQ_USER": "clarinet",
        "CLARINET_TEST_RABBITMQ_PASS": "0123abcd",
        "CLARINET_TEST_RABBITMQ_MANAGEMENT_USER": "clarinet",
        "CLARINET_TEST_RABBITMQ_MANAGEMENT_PASS": "0123abcd",
        "CLARINET_TEST_REQUIRE_RABBITMQ": "1",
    }
    assert _eval_in_sh(env, *names) == list(names.values())


def test_an_operators_values_do_not_survive(tmp_path: Path) -> None:
    """A remapped port in .env.test would otherwise make the pipeline tests skip."""
    env = _env(tmp_path, login="clarinet", password="0123abcd")
    env["CLARINET_TEST_RABBITMQ_PORT"] = "5673"
    env["CLARINET_TEST_RABBITMQ_MANAGEMENT_USER"] = "clarinet_test"
    assert _eval_in_sh(
        env, "CLARINET_TEST_RABBITMQ_PORT", "CLARINET_TEST_RABBITMQ_MANAGEMENT_USER"
    ) == ["5672", "clarinet"]


def test_values_are_quoted_for_eval(tmp_path: Path) -> None:
    password = "it's $HOME `id` \\ ok"
    env = _env(tmp_path, login="clarinet", password=password)
    assert _eval_in_sh(env, "CLARINET_TEST_RABBITMQ_PASS") == [password]


def test_unreadable_credentials_fail_without_exports(tmp_path: Path) -> None:
    env = _env(tmp_path, login="clarinet", password="")
    result = subprocess.run(
        ["bash", str(SCRIPT), VM_IP], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "rabbitmq_password" in result.stderr
