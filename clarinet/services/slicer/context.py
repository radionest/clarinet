"""Slicer script context builder.

Automatically injects standard variables and file paths from the file registry
into the context dict sent to Slicer scripts.

Layers (later overrides earlier):
1. Standard vars: working_folder, study_uid, series_uid (by level)
2. File paths: each FileDefinition -> resolved absolute path (by fd.name)
3. output_file: first OUTPUT file from file_registry (convenience alias)
4. Custom slicer_script_args (template-resolved with all vars above)
5. Custom slicer_result_validator_args (same)
"""

from pathlib import PurePosixPath
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileRole
from clarinet.models.record import RecordRead
from clarinet.services.pipeline.context import FileResolver
from clarinet.services.slicer.context_hydration import hydrate_slicer_context
from clarinet.settings import settings
from clarinet.utils.file_patterns import resolve_origin_type
from clarinet.utils.logger import logger


def build_template_vars(record: RecordRead) -> dict[str, Any]:
    """Build template variables for custom slicer_script_args resolution.

    Provides the placeholder set accepted by ``RecordRead._format_path``
    (UX mode) — ``{study_anon_uid}``, ``{patient_id}`` etc. — so that
    custom Slicer script args resolve.

    UX layer — falls back to raw UIDs when anonymization has not yet
    propagated, so the Slicer UI keeps rendering text for the doctor
    even on records that predate the anonymization run. Backend tasks
    that need a real anonymized path must use ``FileResolver`` /
    ``build_context`` in safe-by-default mode instead.

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


def _translate_paths_for_client(
    context: dict[str, Any],
    server_base: str,
) -> dict[str, Any]:
    """Replace server storage path prefix with client-accessible prefix.

    Uses PurePosixPath.relative_to() for safe boundary matching —
    prevents partial prefix hits (e.g. /mnt/vol vs /mnt/vol2).
    Non-path values raise ValueError on relative_to() and pass through unchanged.
    """
    client_base = settings.storage_path_client
    if not client_base:
        return context

    server = PurePosixPath(server_base)
    client_base = client_base.rstrip("/\\")

    translated = {}
    translations: list[str] = []
    for key, value in context.items():
        if not isinstance(value, str):
            translated[key] = value
            continue
        try:
            rel = PurePosixPath(value).relative_to(server)
            suffix = f"/{rel}" if str(rel) != "." else ""
            translated[key] = client_base + suffix
            translations.append(f"{key}: {value} -> {translated[key]}")
        except ValueError:
            translated[key] = value

    if translations:
        logger.debug(
            f"Slicer path translation (server={server_base} -> client={client_base}): {translations}"
        )

    return translated


def build_slicer_context(
    record: RecordRead,
    *,
    parent: RecordRead | None = None,
) -> dict[str, Any]:
    """Build complete Slicer script context from a record.

    Layers (later overrides earlier):
    1. Standard vars: working_folder, study_uid, series_uid (by level)
    2. File paths: each FileDefinition -> resolved absolute path (by fd.name)
    3. output_file: first OUTPUT file from file_registry (convenience alias)
    4. Custom slicer_script_args (template-resolved with all vars above)
    5. Custom slicer_result_validator_args (same)

    Args:
        record: Fully-loaded ``RecordRead`` with relations.
        parent: Optional parent record for ``origin_type`` resolution.

    Returns:
        Context dict ready for injection into Slicer scripts.
    """
    context: dict[str, Any] = {}
    level = record.record_type.level
    file_registry = record.record_type.file_registry or []

    # -- Layer 1: Standard variables (by level) --
    # Slicer is the UI layer for the radiologist — open a record even if
    # anonymization has not propagated yet (PACS still answers under the
    # raw UID in that window). Backend tasks must NOT mirror this — they
    # use FileResolver.build_working_dirs in safe-by-default mode.
    context["record_id"] = record.id
    working_dirs = FileResolver.build_working_dirs(record, fallback_to_unanonymized=True)
    record_level = DicomQueryLevel(level) if isinstance(level, str) else level
    context["working_folder"] = str(working_dirs.get(record_level, ""))

    # PACS connection params — used by PacsHelper inside Slicer
    # calling_aet/move_aet intentionally omitted — read from Slicer's QSettings
    context["pacs_host"] = settings.pacs_host
    context["pacs_port"] = settings.pacs_port
    context["pacs_aet"] = settings.pacs_aet
    context["dicom_retrieve_mode"] = settings.dicom_retrieve_mode

    # Layer 1 UIDs flow into PACS retrieve calls inside Slicer
    # (`PacsHelper.retrieve_study(study_uid)` / `.retrieve_series(...)`).
    # On anonymized studies the PACS holds them under ``anon_uid``; on a
    # not-yet-anonymized study it still holds them under the raw UID.
    # Fall back so the doctor can open in-flight records too.
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
        if parent is not None:
            fields["origin_type"] = resolve_origin_type(record, parent)
        resolver = FileResolver(
            working_dirs=working_dirs,
            record_type_level=record_level,
            file_registry=file_registry,
            fields=fields,
        )
        unresolved: list[str] = []
        for fd in file_registry:
            try:
                context[fd.name] = str(resolver.resolve(fd))
            except (KeyError, ValueError) as exc:
                logger.warning(f"Slicer context: could not resolve file '{fd.name}' — {exc}")
                unresolved.append(fd.name)

        if unresolved:
            from clarinet.exceptions.domain import ScriptArgumentError

            raise ScriptArgumentError(
                f"Cannot build Slicer context: unresolved files {unresolved}. "
                f"Record may be missing required UIDs for its level."
            )

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

    return context


async def build_slicer_context_async(
    record: RecordRead,
    session: AsyncSession,
    *,
    parent: RecordRead | None = None,
) -> dict[str, Any]:
    """Build Slicer context with optional async hydration.

    Calls the sync ``build_slicer_context()`` first, then runs any registered
    slicer context hydrators configured on the record type.

    Args:
        record: Fully-loaded ``RecordRead`` with relations.
        session: Async DB session for hydrator queries.
        parent: Optional pre-loaded parent record. When provided, skips the
            DB lookup. When ``None`` and ``record.parent_record_id`` is set,
            the parent is loaded via repository.

    Returns:
        Context dict ready for injection into Slicer scripts.
    """
    # Load parent for origin_type resolution (skip if already provided)
    if parent is None and record.parent_record_id is not None:
        from clarinet.repositories.record_repository import RecordRepository

        repo = RecordRepository(session)
        parent_orm = await repo.get_with_relations(record.parent_record_id)
        if parent_orm:
            parent = RecordRead.model_validate(parent_orm)

    context = build_slicer_context(record, parent=parent)

    hydrator_names = record.record_type.slicer_context_hydrators
    if hydrator_names:
        context = await hydrate_slicer_context(context, record, session, hydrator_names)

    # Path translation — after all layers including hydrators
    server_base = record.clarinet_storage_path or settings.storage_path
    context = _translate_paths_for_client(context, server_base)

    return context
