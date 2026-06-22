"""Tests for deploy/lib/ — logging.sh, common.sh, topology.py, settings_overlay.py."""

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent.parent
LIB_DIR = DEPLOY_DIR / "lib"
TOPOLOGY_PY = LIB_DIR / "topology.py"
SETTINGS_OVERLAY_PY = LIB_DIR / "settings_overlay.py"


def bash(script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a bash snippet, capturing stdout and stderr."""
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=check,
    )


def py(script: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a deploy/lib python helper via the test interpreter."""
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        check=check,
    )


# ── logging.sh ──────────────────────────────────────────────────────────


class TestLogging:
    """Tests for deploy/lib/logging.sh."""

    def source_logging(self) -> str:
        return f"source '{LIB_DIR}/logging.sh'"

    def test_init_logging_defines_functions(self):
        r = bash(f"""
            {self.source_logging()}
            init_logging "test"
            declare -f log >/dev/null
            declare -f warn >/dev/null
            declare -f err >/dev/null
        """)
        assert r.returncode == 0

    def test_log_contains_tag_and_message(self):
        r = bash(f"""
            {self.source_logging()}
            init_logging "mytag"
            log "hello world"
        """)
        assert "[mytag]" in r.stderr
        assert "hello world" in r.stderr

    def test_warn_contains_tag(self):
        r = bash(f"""
            {self.source_logging()}
            init_logging "mytag"
            warn "caution"
        """)
        assert "[mytag]" in r.stderr
        assert "caution" in r.stderr

    def test_err_goes_to_stderr(self):
        r = bash(f"""
            {self.source_logging()}
            init_logging "mytag"
            err "oops"
        """)
        assert "[mytag]" in r.stderr
        assert "oops" in r.stderr
        assert "oops" not in r.stdout

    def test_reinit_changes_tag(self):
        r = bash(f"""
            {self.source_logging()}
            init_logging "first"
            init_logging "second"
            log "msg"
        """)
        assert "[second]" in r.stderr
        assert "[first]" not in r.stderr

    def test_init_logging_requires_argument(self):
        r = bash(
            f"""
            {self.source_logging()}
            init_logging
        """,
            check=False,
        )
        assert r.returncode != 0


# ── common.sh ───────────────────────────────────────────────────────────


class TestCommon:
    """Tests for deploy/lib/common.sh."""

    def source_common(self) -> str:
        return f"source '{LIB_DIR}/common.sh'; init_logging test"

    def test_sources_logging(self):
        r = bash(f"""
            source '{LIB_DIR}/common.sh'
            declare -f init_logging >/dev/null
        """)
        assert r.returncode == 0

    def test_require_commands_succeeds_for_existing(self):
        r = bash(f"""
            {self.source_common()}
            require_commands bash ls
        """)
        assert r.returncode == 0

    def test_require_commands_fails_for_missing(self):
        r = bash(
            f"""
            {self.source_common()}
            require_commands __nonexistent_cmd_xyz__
        """,
            check=False,
        )
        assert r.returncode == 1

    def test_require_commands_error_mentions_command(self):
        r = bash(
            f"""
            {self.source_common()}
            require_commands __nonexistent_cmd_xyz__
        """,
            check=False,
        )
        assert "__nonexistent_cmd_xyz__" in r.stderr

    def test_require_commands_mixed(self):
        """Fails if any command is missing, even if others exist."""
        r = bash(
            f"""
            {self.source_common()}
            require_commands bash __nonexistent_cmd_xyz__
        """,
            check=False,
        )
        assert r.returncode == 1


# ── Source chain integration ────────────────────────────────────────────


class TestSourceChain:
    """Integration tests: verify sourcing works across all scripts."""

    def test_vm_sh_help(self):
        r = bash(f"bash '{DEPLOY_DIR}/vm/vm.sh' help")
        assert "create" in r.stdout
        assert "deploy" in r.stdout

    def test_all_scripts_pass_syntax_check(self):
        scripts = [
            LIB_DIR / "logging.sh",
            LIB_DIR / "common.sh",
            LIB_DIR / "vm-setting.sh",
            LIB_DIR / "vm-setting-write.sh",
            DEPLOY_DIR / "vm" / "vm.sh",
            DEPLOY_DIR / "vm" / "bake-image.sh",
            DEPLOY_DIR / "install" / "install-clarinet.sh",
            DEPLOY_DIR / "install" / "setup-services.sh",
            DEPLOY_DIR / "install" / "generate-settings.sh",
            DEPLOY_DIR / "nginx" / "generate-ssl.sh",
            DEPLOY_DIR / "test" / "deploy-test.sh",
        ]
        for script in scripts:
            r = subprocess.run(
                ["bash", "-n", str(script)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert r.returncode == 0, f"Syntax error in {script.name}: {r.stderr}"

    def test_vm_side_source_chain(self):
        """Simulate target VM layout: scripts under /tmp/clarinet-deploy/."""
        tmpdir = tempfile.mkdtemp(prefix="clarinet-deploy-test-")
        try:
            for subdir in ("lib", "install", "nginx"):
                shutil.copytree(DEPLOY_DIR / subdir, Path(tmpdir) / subdir)

            # generate-settings.sh resolves ../lib/logging.sh from install/
            r = bash(f"""
                SCRIPT_DIR='{tmpdir}/install'
                source "$SCRIPT_DIR/../lib/logging.sh"
                init_logging "settings"
                log "vm-side test"
            """)
            assert "[settings]" in r.stderr
            assert "vm-side test" in r.stderr

            # generate-ssl.sh resolves ../lib/logging.sh from nginx/
            r = bash(f"""
                SCRIPT_DIR='{tmpdir}/nginx'
                source "$SCRIPT_DIR/../lib/logging.sh"
                init_logging "ssl"
                log "ssl test"
            """)
            assert "[ssl]" in r.stderr
        finally:
            shutil.rmtree(tmpdir)


# ── topology.py ──────────────────────────────────────────────────────────

# stand omits ram on purpose — exercises the [defaults] fallback.
_TOPOLOGY_TOML = """
[defaults]
ram = 4096
vcpus = 2
disk_size = 20

[project]
name = "nir_liver"
path_prefix = "/nir_liver/"
source_dir = ""

[vm.stand]
role = "stand"
vcpus = 2

[vm.pacs]
role = "pacs"
ram = 2048
vcpus = 1

[vm.worker]
role = "worker"
ram = 4096
queues = ["default", "dicom"]
"""


class TestTopology:
    """Tests for deploy/lib/topology.py."""

    def _topo_file(self, tmp_path) -> Path:
        f = tmp_path / "demo.toml"
        f.write_text(_TOPOLOGY_TOML)
        return f

    def test_vms_ordered_by_role_rank(self, tmp_path):
        f = self._topo_file(tmp_path)
        r = py(TOPOLOGY_PY, "vms", str(f))
        assert r.stdout.split() == ["pacs", "stand", "worker"]

    def test_get_queues_space_joined(self, tmp_path):
        f = self._topo_file(tmp_path)
        r = py(TOPOLOGY_PY, "get", str(f), "worker", "queues")
        assert r.stdout.strip() == "default dicom"

    def test_get_explicit_value(self, tmp_path):
        f = self._topo_file(tmp_path)
        r = py(TOPOLOGY_PY, "get", str(f), "pacs", "ram")
        assert r.stdout.strip() == "2048"

    def test_get_falls_back_to_defaults(self, tmp_path):
        f = self._topo_file(tmp_path)
        r = py(TOPOLOGY_PY, "get", str(f), "stand", "ram")
        assert r.stdout.strip() == "4096"

    def test_get_missing_returns_default_arg(self, tmp_path):
        f = self._topo_file(tmp_path)
        r = py(TOPOLOGY_PY, "get", str(f), "worker", "role")
        assert r.stdout.strip() == "worker"
        r = py(TOPOLOGY_PY, "get", str(f), "pacs", "queues", "")
        assert r.stdout.strip() == ""

    def test_project_value(self, tmp_path):
        f = self._topo_file(tmp_path)
        r = py(TOPOLOGY_PY, "project", str(f), "path_prefix")
        assert r.stdout.strip() == "/nir_liver/"

    def test_file_resolves_bundled_name(self):
        r = py(TOPOLOGY_PY, "file", "nir_liver")
        resolved = Path(r.stdout.strip())
        assert resolved == (DEPLOY_DIR / "vm" / "topologies" / "nir_liver.toml").resolve()
        assert resolved.is_file()

    def test_file_missing_exits_2(self):
        r = py(TOPOLOGY_PY, "file", "no_such_topology_xyz", check=False)
        assert r.returncode == 2

    def test_lock_write_get_round_trip(self, tmp_path):
        lock = tmp_path / "topo.lock.json"
        payload = json.dumps(
            {
                "topology": "demo",
                "vms": {
                    "stand": {
                        "role": "stand",
                        "vm_name": "clarinet-demo-stand",
                        "ip": "192.168.122.10",
                    },
                },
            }
        )
        py(TOPOLOGY_PY, "lock-write", str(lock), payload)
        for field, expected in (
            ("role", "stand"),
            ("vm_name", "clarinet-demo-stand"),
            ("ip", "192.168.122.10"),
        ):
            r = py(TOPOLOGY_PY, "lock-get", str(lock), "stand", field)
            assert r.stdout.strip() == expected

    def test_lock_get_absent_is_empty(self, tmp_path):
        lock = tmp_path / "topo.lock.json"
        lock.write_text(json.dumps({"vms": {}}))
        r = py(TOPOLOGY_PY, "lock-get", str(lock), "stand", "ip")
        assert r.stdout.strip() == ""

    def test_lock_get_malformed_shape_is_empty(self, tmp_path):
        # Valid JSON, wrong shape (top-level array) must degrade to '' not crash.
        lock = tmp_path / "topo.lock.json"
        lock.write_text("[1, 2, 3]")
        r = py(TOPOLOGY_PY, "lock-get", str(lock), "stand", "ip")
        assert r.returncode == 0
        assert r.stdout.strip() == ""

    def test_key_of_role(self, tmp_path):
        f = self._topo_file(tmp_path)
        for role in ("pacs", "stand", "worker"):
            r = py(TOPOLOGY_PY, "key-of-role", str(f), role)
            assert r.stdout.strip() == role

    def test_key_of_role_resolves_renamed_key(self, tmp_path):
        f = tmp_path / "renamed.toml"
        f.write_text('[vm.worker_gpu]\nrole = "worker"\nram = 2048\nvcpus = 1\n')
        r = py(TOPOLOGY_PY, "key-of-role", str(f), "worker")
        assert r.stdout.strip() == "worker_gpu"

    def test_key_of_role_absent_is_empty(self, tmp_path):
        f = self._topo_file(tmp_path)
        r = py(TOPOLOGY_PY, "key-of-role", str(f), "nonesuch")
        assert r.stdout.strip() == ""


# ── settings_overlay.py ──────────────────────────────────────────────────

_OVERLAY_HEADER = "# Clarinet stand overrides (layered over the project's settings.toml)"


class TestSettingsOverlay:
    """Tests for deploy/lib/settings_overlay.py."""

    def test_upsert_into_empty_file(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "pacs_host=1.2.3.4", "dicom_retrieve_mode=c-get")
        data = tomllib.loads(target.read_text())
        assert data == {"pacs_host": "1.2.3.4", "dicom_retrieve_mode": "c-get"}

    def test_first_line_is_recognised_header(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "pacs_host=1.2.3.4")
        assert target.read_text().splitlines()[0] == _OVERLAY_HEADER

    def test_idempotent_bytes(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "pacs_host=1.2.3.4", "pacs_port=4242")
        first = target.read_bytes()
        py(SETTINGS_OVERLAY_PY, str(target), "pacs_host=1.2.3.4", "pacs_port=4242")
        assert target.read_bytes() == first

    def test_upsert_preserves_prior_keys(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "pacs_host=1.2.3.4")
        py(SETTINGS_OVERLAY_PY, str(target), "rabbitmq_host=5.6.7.8")
        data = tomllib.loads(target.read_text())
        assert data == {"pacs_host": "1.2.3.4", "rabbitmq_host": "5.6.7.8"}

    def test_overwrites_existing_key(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "pacs_host=1.2.3.4")
        py(SETTINGS_OVERLAY_PY, str(target), "pacs_host=9.9.9.9")
        data = tomllib.loads(target.read_text())
        assert data["pacs_host"] == "9.9.9.9"

    def test_bool_and_int_coercion(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "api_verify_ssl=false", "pacs_port=4242")
        data = tomllib.loads(target.read_text())
        assert data["api_verify_ssl"] is False
        assert data["pacs_port"] == 4242
        assert isinstance(data["pacs_port"], int)

    def test_dotted_value_stays_string(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "api_base_url=https://10.0.0.1/nir_liver/api")
        data = tomllib.loads(target.read_text())
        assert data["api_base_url"] == "https://10.0.0.1/nir_liver/api"

    def test_all_digit_secret_stays_string(self, tmp_path):
        # openssl rand -hex can yield an all-digit secret; it must NOT coerce to
        # int (drops leading zero + changes type -> worker auth breaks).
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "admin_password=0123456789012345")
        data = tomllib.loads(target.read_text())
        assert data["admin_password"] == "0123456789012345"
        assert isinstance(data["admin_password"], str)

    def test_secret_literal_false_stays_string(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "rabbitmq_password=false")
        data = tomllib.loads(target.read_text())
        assert data["rabbitmq_password"] == "false"

    def test_leading_zero_non_secret_stays_string(self, tmp_path):
        target = tmp_path / "settings.custom.toml"
        py(SETTINGS_OVERLAY_PY, str(target), "some_code=007")
        data = tomllib.loads(target.read_text())
        assert data["some_code"] == "007"


# ── python helper syntax ─────────────────────────────────────────────────


class TestPythonHelpers:
    """Both deploy/lib python helpers must byte-compile cleanly."""

    def test_helpers_compile(self):
        for module in (TOPOLOGY_PY, SETTINGS_OVERLAY_PY):
            r = subprocess.run(
                [sys.executable, "-m", "py_compile", str(module)],
                capture_output=True,
                text=True,
                check=False,
            )
            assert r.returncode == 0, f"py_compile failed for {module.name}: {r.stderr}"
