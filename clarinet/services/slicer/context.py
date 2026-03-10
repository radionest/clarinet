"""Slicer script context builder.

Automatically injects standard variables, file paths from the file registry,
and PACS settings into the context dict sent to Slicer scripts.

Layers (later overrides earlier):
1. Standard vars: working_folder, study_uid, series_uid (by level)
2. File paths: each FileDefinition -> resolved absolute path (by fd.name)
3. output_file: first OUTPUT file from file_registry (convenience alias)
4. Custom slicer_script_args (template-resolved with all vars above)
5. Custom slicer_result_validator_args (same)
6. PACS settings
"""

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileRole
from clarinet.services.pipeline.context import FileResolver
from clarinet.services.slicer.context_hydration import hydrate_slicer_context
from clarinet.settings import settings
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from clarinet.models.record import RecordRead


def build_template_vars(record: RecordRead) -> dict[str, Any]:
    """Build template variables for custom slicer_script_args resolution.

    Provides the same set of placeholders as ``RecordRead._format_path_strict()``,
    so that ``{study_anon_uid}``, ``{patient_id}`` etc. work in custom args.

    Args:
        record: Fully-loaded record with relations.

    Returns:
        Flat dict of placeholder values.
    """
    return {
        "patient_id": (
            record.patient.anon_id if record.patient.anon_id is not None else record.patient_id
        ),
        "patient_anon_name": record.patient.anon_name,
        "study_uid": record.study_uid,
        "study_anon_uid": (
            (record.study.anon_uid if record.study else record.study_anon_uid) or record.study_uid
        ),
        "series_uid": record.series_uid,
        "series_anon_uid": (
            (record.series.anon_uid if record.series else record.series_anon_uid)
            or record.series_uid
        ),
        "user_id": record.user_id,
        "clarinet_storage_path": record.clarinet_storage_path or settings.storage_path,
    }


def _build_pacs_context() -> dict[str, Any]:
    """Build PACS settings context dict for Slicer scripts."""
    return {
        "pacs_host": settings.pacs_host,
        "pacs_port": settings.pacs_port,
        "pacs_aet": settings.pacs_aet,
        "pacs_calling_aet": settings.pacs_calling_aet,
        "pacs_prefer_cget": settings.pacs_prefer_cget,
        "pacs_move_aet": settings.pacs_move_aet,
    }


def _resolve_custom_args(
    args: dict[str, str] | None,
    format_vars: dict[str, Any],
) -> dict[str, str]:
    """Resolve template placeholders in custom slicer args.

    Args:
        args: Dict of arg_name -> template string (e.g. ``{study_anon_uid}``).
        format_vars: All available placeholder values.

    Returns:
        Dict of resolved args. Unresolvable templates are logged and skipped.
    """
    if not args:
        return {}
    resolved: dict[str, str] = {}
    for key, template in args.items():
        try:
            resolved[key] = template.format(**format_vars)
        except (KeyError, AttributeError) as exc:
            logger.warning(f"Slicer arg '{key}': unresolved template '{template}' — {exc}")
    return resolved


def build_slicer_context(record: RecordRead) -> dict[str, Any]:
    """Build complete Slicer script context from a record.

    Layers (later overrides earlier):
    1. Standard vars: working_folder, study_uid, series_uid (by level)
    2. File paths: each FileDefinition -> resolved absolute path (by fd.name)
    3. output_file: first OUTPUT file from file_registry (convenience alias)
    4. Custom slicer_script_args (template-resolved with all vars above)
    5. Custom slicer_result_validator_args (same)
    6. PACS settings

    Args:
        record: Fully-loaded ``RecordRead`` with relations.

    Returns:
        Context dict ready for injection into Slicer scripts.
    """
    context: dict[str, Any] = {}
    level = record.record_type.level
    file_registry = record.record_type.file_registry or []

    # -- Layer 1: Standard variables (by level) --
    working_dirs = FileResolver.build_working_dirs(record)
    record_level = DicomQueryLevel(level) if isinstance(level, str) else level
    context["working_folder"] = str(working_dirs.get(record_level, ""))

    if record_level in (DicomQueryLevel.STUDY, DicomQueryLevel.SERIES):
        context["study_uid"] = (
            record.study.anon_uid if record.study else record.study_anon_uid
        ) or record.study_uid

    if record_level == DicomQueryLevel.SERIES:
        context["series_uid"] = (
            record.series.anon_uid if record.series else record.series_anon_uid
        ) or record.series_uid

    # -- Layer 2: File paths from file_registry --
    if file_registry:
        fields = FileResolver.build_fields(record)
        resolver = FileResolver(
            working_dirs=working_dirs,
            record_type_level=record_level,
            file_registry=file_registry,
            fields=fields,
        )
        for fd in file_registry:
            try:
                context[fd.name] = str(resolver.resolve(fd))
            except (KeyError, ValueError) as exc:
                logger.warning(f"Slicer context: could not resolve file '{fd.name}' — {exc}")

        # -- Layer 3: output_file convenience alias --
        output_files = [fd for fd in file_registry if fd.role == FileRole.OUTPUT]
        if output_files:
            first_output = output_files[0]
            if first_output.name in context:
                context["output_file"] = context[first_output.name]

    # -- Layer 4 & 5: Custom args (template-resolved) --
    template_vars = build_template_vars(record)
    format_vars = {**template_vars, **context}

    if record.record_type.slicer_script_args:
        context.update(_resolve_custom_args(record.record_type.slicer_script_args, format_vars))

    if record.record_type.slicer_result_validator_args:
        context.update(
            _resolve_custom_args(record.record_type.slicer_result_validator_args, format_vars)
        )

    # -- Layer 6: PACS settings --
    context.update(_build_pacs_context())

    return context


async def build_slicer_context_async(
    record: RecordRead,
    session: AsyncSession,
) -> dict[str, Any]:
    """Build Slicer context with optional async hydration.

    Calls the sync ``build_slicer_context()`` first, then runs any registered
    slicer context hydrators configured on the record type.

    Args:
        record: Fully-loaded ``RecordRead`` with relations.
        session: Async DB session for hydrator queries.

    Returns:
        Context dict ready for injection into Slicer scripts.
    """
    context = build_slicer_context(record)

    hydrator_names = record.record_type.slicer_context_hydrators
    if hydrator_names:
        context = await hydrate_slicer_context(context, record, session, hydrator_names)

    return context
