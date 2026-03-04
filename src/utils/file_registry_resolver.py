"""Project-level file registry resolver.

Resolves file references in task configs against a shared file registry
(``file_registry.toml`` or ``file_registry.json``) so that file definitions
can be defined once and reused across multiple RecordTypes.
"""

import json
import os
import tomllib
from typing import Any

import aiofiles
from sqlmodel import SQLModel

from src.exceptions.domain import ValidationError
from src.models.file_schema import FileDefinition, FileRole
from src.utils.logger import logger


class FileReference(SQLModel):
    """Reference to a file defined in the project-level file registry.

    Attributes:
        name: Name matching a key in the file registry (TOML or JSON)
        role: File role (input/output/intermediate)
        required: Whether this file is required
    """

    name: str
    role: FileRole = FileRole.OUTPUT
    required: bool = True


async def load_project_file_registry(folder: str) -> dict[str, Any] | None:
    """Load the project-level file registry from *folder*.

    Checks for ``file_registry.toml`` first, then ``file_registry.json``.
    TOML takes precedence when both exist.

    Args:
        folder: Directory that may contain ``file_registry.toml`` or
            ``file_registry.json``.

    Returns:
        Parsed dict mapping file names to their definitions, or ``None``
        if neither file exists.
    """
    toml_path = os.path.join(folder, "file_registry.toml")
    json_path = os.path.join(folder, "file_registry.json")

    if os.path.isfile(toml_path):
        async with aiofiles.open(toml_path) as f:
            content = await f.read()
        registry: dict[str, Any] = tomllib.loads(content)
        logger.info(f"Loaded project file registry with {len(registry)} entries from {toml_path}")
        return registry

    if os.path.isfile(json_path):
        async with aiofiles.open(json_path) as f:
            content = await f.read()
        registry = json.loads(content)
        logger.info(f"Loaded project file registry with {len(registry)} entries from {json_path}")
        return registry

    return None


def resolve_file_references(
    files: list[dict[str, Any]],
    registry: dict[str, Any],
) -> list[FileDefinition]:
    """Merge file references with the project-level registry.

    Each entry in *files* must have a ``name`` key that matches a key in
    *registry*. The registry entry provides ``pattern``, ``description``,
    and ``multiple``, while the reference provides ``role`` and ``required``.

    Args:
        files: List of file reference dicts (``name``, ``role``, ``required``).
        registry: Project file registry mapping names to definition data.

    Returns:
        List of fully resolved ``FileDefinition`` objects.

    Raises:
        ValidationError: If a reference name is not found in the registry.
    """
    resolved: list[FileDefinition] = []
    for ref_dict in files:
        ref = FileReference(**ref_dict)
        entry = registry.get(ref.name)
        if entry is None:
            raise ValidationError(
                f"File reference '{ref.name}' not found in project file registry. "
                f"Available: {', '.join(registry.keys())}"
            )
        resolved.append(
            FileDefinition(
                name=ref.name,
                pattern=entry["pattern"],
                description=entry.get("description"),
                required=ref.required,
                multiple=entry.get("multiple", False),
                role=ref.role,
            )
        )
    return resolved


def resolve_task_files(
    props: dict[str, Any],
    registry: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve ``files`` references in task properties dict.

    If *props* has a ``"files"`` key, resolve references against *registry*
    and replace with ``"file_registry"``. If *props* already has
    ``"file_registry"``, pass through unchanged. Having both is an error.

    Args:
        props: Task properties dict (mutated in place and returned).
        registry: Project file registry, or ``None`` if unavailable.

    Returns:
        The (possibly mutated) *props* dict.

    Raises:
        ValidationError: If both ``files`` and ``file_registry`` are present,
            or if ``files`` is used but no registry is available.
    """
    has_files = "files" in props
    has_file_registry = "file_registry" in props

    if has_files and has_file_registry:
        raise ValidationError("Task definition must not have both 'files' and 'file_registry' keys")

    if not has_files:
        return props

    if registry is None:
        raise ValidationError(
            "Task uses 'files' references but no project file_registry.toml/.json was found"
        )

    files_refs = props.pop("files")
    resolved = resolve_file_references(files_refs, registry)
    props["file_registry"] = [fd.model_dump() for fd in resolved]
    return props
