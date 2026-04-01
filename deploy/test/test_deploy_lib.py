"""Tests for deploy/lib/ — logging.sh and common.sh."""

import shutil
import subprocess
import tempfile
from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parent.parent
LIB_DIR = DEPLOY_DIR / "lib"


def bash(script: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a bash snippet, capturing stdout and stderr."""
    return subprocess.run(
        ["bash", "-c", script],
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
            DEPLOY_DIR / "vm" / "vm.sh",
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
