"""Generate deployment configurations for Clarinet.

Supports systemd unit file generation with auto-detection of the current
environment (executable path, user, working directory, settings).
"""

import argparse
import getpass
import grp
import os
import shutil
import sys
from pathlib import Path
from string import Template

from clarinet.settings import settings
from clarinet.utils.logger import logger


def _get_clarinet_executable() -> str:
    """Find the absolute path to the clarinet executable.

    Returns:
        Absolute path to the clarinet binary or a python -m fallback.
    """
    # 1. Check venv bin directory
    venv_bin = Path(sys.executable).parent / "clarinet"
    if venv_bin.is_file():
        return str(venv_bin.resolve())

    # 2. Check PATH
    which_result = shutil.which("clarinet")
    if which_result:
        return str(Path(which_result).resolve())

    # 3. Fallback to python -m
    return f"{sys.executable} -m clarinet"


def _detect_environment() -> dict[str, str]:
    """Auto-detect environment parameters for systemd unit generation.

    Returns:
        Dictionary with template variables.
    """
    working_directory = os.getcwd()

    # Deduplicate read-write paths
    rw_paths: set[str] = {
        str(Path(settings.storage_path).resolve()),
        str(settings.get_log_dir().resolve()),
        working_directory,
    }

    return {
        "exec_path": _get_clarinet_executable(),
        "user": getpass.getuser(),
        "group": grp.getgrgid(os.getgid()).gr_name,
        "working_directory": working_directory,
        "host": settings.host,
        "port": str(settings.port),
        "env_file": f"{working_directory}/env",
        "read_write_paths": " ".join(sorted(rw_paths)),
        "workers": "2",
    }


def _load_template(name: str) -> Template:
    """Load a string.Template from the clarinet/deploy/ package directory.

    Args:
        name: Template filename (e.g. 'clarinet-api.service.template').

    Returns:
        Populated string.Template instance.
    """
    import clarinet

    template_path = Path(clarinet.__file__).parent / "deploy" / name
    return Template(template_path.read_text())


def generate_systemd(args: argparse.Namespace) -> None:
    """Generate systemd unit files for Clarinet services.

    Args:
        args: Parsed CLI arguments with optional overrides.
    """
    env = _detect_environment()

    # Apply CLI overrides
    if args.user:
        env["user"] = args.user
    if args.group:
        env["group"] = args.group
    if args.working_dir:
        env["working_directory"] = args.working_dir
    if args.workers:
        env["workers"] = str(args.workers)
    if args.env_file:
        env["env_file"] = args.env_file

    output_dir = Path(args.output_dir) if args.output_dir else None

    # Always generate API unit
    api_template = _load_template("clarinet-api.service.template")
    api_content = api_template.substitute(env)
    _write_unit("clarinet-api.service", api_content, output_dir)

    # Generate worker unit only if pipeline is enabled
    if settings.pipeline_enabled:
        worker_template = _load_template("clarinet-worker@.service.template")
        worker_content = worker_template.substitute(env)
        _write_unit("clarinet-worker@.service", worker_content, output_dir)
    else:
        logger.info(
            "Pipeline disabled — skipping worker unit. "
            "Set CLARINET_PIPELINE_ENABLED=true to generate it."
        )

    # Print installation instructions to stderr
    _print_instructions(output_dir)


def _write_unit(filename: str, content: str, output_dir: Path | None) -> None:
    """Write a unit file to output_dir or stdout.

    Args:
        filename: Name of the unit file.
        content: Rendered unit file content.
        output_dir: Directory to write to, or None for stdout.
    """
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / filename
        path.write_text(content)
        logger.info(f"Written: {path}")
    else:
        print(f"# --- {filename} ---")
        print(content)


def _print_instructions(output_dir: Path | None) -> None:
    """Print installation instructions to stderr.

    Args:
        output_dir: Directory where files were written, or None if stdout.
    """
    src = output_dir or "<save files first>"

    instructions = f"""
# Installation instructions:
#   sudo cp {src}/clarinet-*.service /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable --now clarinet-api.service
#   sudo systemctl enable --now clarinet-worker@gpu.service
#   sudo systemctl enable --now clarinet-worker@cpu.service
"""
    print(instructions, file=sys.stderr)
