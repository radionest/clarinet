"""Locate the packaged project-scaffold payload (`clarinet init`).

The payload lives inside the package so it ships in every wheel — resolved off
``clarinet.__file__`` like ``agent_scaffold._package_docs_dir``. Files whose
target name starts with a dot are stored undotted: a ``.gitignore`` inside the
package would govern what git tracks there, and therefore what the build
backend ships.
"""

from pathlib import Path

import clarinet
from clarinet.exceptions.domain import ProjectScaffoldError

# payload name → name written into the target project
SCAFFOLD_DOTFILES: dict[str, str] = {
    "gitignore": ".gitignore",
    "env.example": ".env.example",
}


def scaffold_source_dir() -> Path:
    """Absolute path of the shipped scaffold payload.

    Raises:
        ProjectScaffoldError: the payload is missing (e.g. a wheel built without
            ``clarinet/scaffold``).
    """
    src = Path(clarinet.__file__).resolve().parent / "scaffold"
    if not src.is_dir():
        raise ProjectScaffoldError(f"project scaffold payload not found at {src}")
    return src
