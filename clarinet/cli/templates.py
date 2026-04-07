"""Template discovery, listing, and copy logic for `clarinet init --template`."""

import shutil
from pathlib import Path

from clarinet.utils.logger import logger

# friendly name → (directory name under examples/, one-line description)
TEMPLATES: dict[str, tuple[str, str]] = {
    "minimal": ("demo_init", "Empty project skeleton"),
    "demo": ("demo", "JSON-mode demo with tasks and record flow"),
    "liver": ("demo_liver", "Liver study (TOML config)"),
    "bigliver": ("demo_liver_v2", "Full liver study (Python config, workflows, pipeline)"),
    "research": (
        "project_template",
        "Research project scaffold (Python config, plan/, .claude/ docs for agents)",
    ),
}

# Patterns to exclude when copying a template.
# NB: ``.claude`` itself is intentionally NOT in this list — templates may ship
# CLAUDE.md and rules/ for agents. We only exclude per-developer local files.
_IGNORE_PATTERNS = (
    "data",
    "__pycache__",
    "settings.local.json",
    "worktrees",
    "*.db",
    "*.log",
    "*.docx",
    "*.pyc",
    "test_dataset",
    "dicomweb_cache",
)


def find_examples_dir() -> Path:
    """Resolve the ``examples/`` directory relative to the clarinet package."""
    import clarinet

    return Path(clarinet.__file__).parent.parent / "examples"


def list_templates() -> None:
    """Print a table of available project templates."""
    print("Available templates:\n")
    print(f"  {'Name':<12} {'Description'}")
    print(f"  {'----':<12} {'-----------'}")
    for name, (_, description) in TEMPLATES.items():
        print(f"  {name:<12} {description}")
    print("\nUsage: clarinet init --template <name> [path]")


def copy_template(template_name: str, dest: str) -> None:
    """Copy a template into *dest*, creating essential subdirectories.

    Args:
        template_name: Friendly template name (case-insensitive).
        dest: Destination directory path.

    Raises:
        SystemExit: If the template name is unknown or the source is missing.
    """
    import sys

    key = template_name.lower()
    if key not in TEMPLATES:
        logger.error(f"Unknown template: '{template_name}'")
        list_templates()
        sys.exit(1)

    dir_name, description = TEMPLATES[key]
    examples_dir = find_examples_dir()
    src = examples_dir / dir_name

    if not src.exists():
        logger.error(f"Template source directory not found: {src}")
        sys.exit(1)

    dest_path = Path(dest).resolve()
    logger.info(f"Copying template '{key}' ({description}) → {dest_path}")

    shutil.copytree(
        src,
        dest_path,
        ignore=shutil.ignore_patterns(*_IGNORE_PATTERNS),
        dirs_exist_ok=True,
    )

    # Ensure essential directories exist
    for subdir in ("tasks", "static", "data"):
        (dest_path / subdir).mkdir(parents=True, exist_ok=True)

    logger.info(f"Template '{key}' applied successfully")
