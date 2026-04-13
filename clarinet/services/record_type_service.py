"""Service layer for RecordType-related business logic.

Extracts create, update, and data-validation logic that previously lived
in the record router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator, SchemaError

from clarinet.exceptions.domain import ValidationError
from clarinet.models.record import RecordType
from clarinet.services.schema_hydration import hydrate_schema
from clarinet.utils.file_link_sync import sync_file_links
from clarinet.utils.validation import validate_json_by_schema, validate_json_by_schema_partial

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from clarinet.models import Record
    from clarinet.models.record import RecordTypeCreate, RecordTypeOptional
    from clarinet.repositories.file_definition_repository import FileDefinitionRepository
    from clarinet.repositories.record_type_repository import RecordTypeRepository
    from clarinet.types import RecordData


class RecordTypeService:
    """Service for RecordType create/update and record-data validation.

    Args:
        record_type_repo: RecordType repository instance.
        fd_repo: FileDefinition repository instance.
        session: Async DB session (needed by ``sync_file_links`` and ``hydrate_schema``).
    """

    def __init__(
        self,
        record_type_repo: RecordTypeRepository,
        fd_repo: FileDefinitionRepository,
        session: AsyncSession,
    ):
        self.repo = record_type_repo
        self.fd_repo = fd_repo
        self.session = session

    async def create_record_type(
        self,
        record_type: RecordTypeCreate,
        constrain_unique_names: bool = True,
    ) -> RecordType:
        """Create a new RecordType with file links.

        Args:
            record_type: DTO for the new record type.
            constrain_unique_names: Enforce name uniqueness.

        Returns:
            Created RecordType with file links eagerly loaded.

        Raises:
            ValidationError: If data_schema is invalid JSON Schema.
            RecordTypeAlreadyExistsError: If name already taken.
            RecordTypeNotFoundError: If parent type doesn't exist.
        """
        file_defs = record_type.file_registry or []

        if record_type.data_schema is not None:
            _validate_json_schema(record_type.data_schema)

        if constrain_unique_names:
            await self.repo.ensure_unique_name(record_type.name)

        create_data = record_type.model_dump(exclude={"file_registry"})
        new_rt = RecordType(**create_data)
        new_rt.file_links = []
        self.session.add(new_rt)
        await self.session.flush()

        if file_defs:
            await sync_file_links(new_rt, file_defs, self.fd_repo, self.session)

        await self.session.commit()
        return await self.repo.get(new_rt.name)

    async def update_record_type(
        self,
        record_type_id: str,
        update: RecordTypeOptional,
    ) -> RecordType:
        """Update an existing RecordType.

        Args:
            record_type_id: Name/ID of the record type.
            update: Partial update DTO.

        Returns:
            Updated RecordType with file links eagerly loaded.

        Raises:
            ValidationError: If data_schema is invalid JSON Schema.
            RecordTypeNotFoundError: If record type or parent doesn't exist.
        """
        record_type = await self.repo.get(record_type_id)

        if update.data_schema is not None:
            _validate_json_schema(update.data_schema)

        file_defs_set = "file_registry" in update.model_fields_set
        file_defs = update.file_registry if file_defs_set else None
        update_data: dict[str, Any] = update.model_dump(
            exclude_unset=True,
            exclude_none=True,
            exclude={"file_registry"},
        )

        if update_data:
            await self.repo.update(record_type, update_data)

        if file_defs is not None:
            current = await self.repo.get(record_type_id)
            await sync_file_links(
                current, file_defs, self.fd_repo, self.session, clear_existing=True
            )
            await self.session.commit()

        return await self.repo.get(record_type_id)

    async def validate_record_data(self, record: Record, data: RecordData) -> RecordData:
        """Validate record data against its hydrated record type schema.

        Resolves ``x-options`` markers to ``oneOf`` before validation.
        Record must have ``record_type`` relation loaded.

        Args:
            record: Record with record_type loaded.
            data: Data to validate.

        Returns:
            Validated data.

        Raises:
            ValidationError: If data does not match schema.
        """
        if record.record_type.data_schema:
            hydrated = await hydrate_schema(record.record_type.data_schema, record, self.session)
            validate_json_by_schema(data, hydrated)

        return data

    async def validate_record_data_partial(self, record: Record, data: RecordData) -> RecordData:
        """Validate record data against schema with ``required`` constraints removed.

        Same as :meth:`validate_record_data` but allows missing fields.
        Use for prefill where data is intentionally incomplete.

        Args:
            record: Record with record_type loaded.
            data: Data to validate.

        Returns:
            Validated data.

        Raises:
            ValidationError: If data violates type/format constraints.
        """
        if record.record_type.data_schema:
            hydrated = await hydrate_schema(record.record_type.data_schema, record, self.session)
            validate_json_by_schema_partial(data, hydrated)

        return data


def _validate_json_schema(schema: dict) -> None:
    """Validate that a dict is a valid JSON Schema (Draft 2020-12).

    Raises:
        ValidationError: If schema is invalid.
    """
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        raise ValidationError(f"Data schema is invalid: {e}") from e
