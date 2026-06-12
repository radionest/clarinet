"""Custom Python validators for RecordData — beyond JSON Schema.

JSON Schema covers structural/type constraints but cannot express cross-field
or cross-element invariants (e.g. "value of one property must be unique among
array elements", or "field X must be in DB table Y"). This module provides a
decorator-based registry, mirroring the ``schema_hydration`` /
``slicer_context_hydration`` pattern, so downstream projects can plug
Python-level validation into the submit/update pipeline.

Downstream projects place validators in ``plan/validators.py`` (filename from
``settings.config_validators_file``) and bind them to a RecordType via
``RecordDef.data_validators=["module.validator_name"]``. The loader imports
the file at app startup (lifespan); decorators register functions in a
module-level registry. ``reconcile_config`` raises ``ConfigurationError`` if a
RecordType references an unregistered name.

See :doc:`.claude/rules/record-data-validator.md` for the contract and a
worked example.
"""

import inspect
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.config.custom_registry import CustomCodeRegistry
from clarinet.exceptions.domain import FieldError, RecordDataValidationError
from clarinet.models.record import Record
from clarinet.repositories.record_repository import RecordRepository
from clarinet.repositories.record_type_repository import RecordTypeRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.repositories.user_repository import UserRepository
from clarinet.types import RecordData
from clarinet.utils.logger import logger


@dataclass(frozen=True, slots=True)
class ValidatorContext:
    """Pre-built dependencies available to record-data validator callbacks.

    Validators receive this context instead of a raw ``AsyncSession``,
    ensuring they only access data through repository interfaces.
    All repositories share the request's session — sequential ``await`` only,
    no ``asyncio.gather`` (see ``clarinet/CLAUDE.md``).
    """

    record_repo: RecordRepository
    study_repo: StudyRepository
    user_repo: UserRepository
    record_type_repo: RecordTypeRepository

    @classmethod
    def from_session(cls, session: AsyncSession) -> "ValidatorContext":
        """Build context from a DB session.

        Args:
            session: Async DB session for the current request.
        """
        return cls(
            record_repo=RecordRepository(session),
            study_repo=StudyRepository(session),
            user_repo=UserRepository(session),
            record_type_repo=RecordTypeRepository(session),
        )


type RecordValidatorFunc = Callable[
    [Record, RecordData, ValidatorContext],
    Coroutine[Any, Any, None],
]


@dataclass(frozen=True, slots=True)
class ValidatorSpec:
    """Registry entry: callable + per-validator flags.

    ``run_on_partial`` lives here (not as a function attribute) so it survives
    ``functools.wraps`` and remains visible to introspection in tests.
    """

    func: RecordValidatorFunc
    run_on_partial: bool


_VALIDATOR_REGISTRY: CustomCodeRegistry[ValidatorSpec] = CustomCodeRegistry(
    filename_setting="config_validators_file",
    label="record validator",
)


def get_registered_validator_names() -> frozenset[str]:
    """Return the set of currently registered validator names.

    Public accessor for the module-private ``_VALIDATOR_REGISTRY`` —
    intended for reconcile-time validation in :func:`bootstrap.reconcile_config`
    and similar consumers that need to check membership without coupling to
    the registry's internal structure.
    """
    return _VALIDATOR_REGISTRY.names()


def record_validator(
    name: str, *, run_on_partial: bool = False
) -> Callable[[RecordValidatorFunc], RecordValidatorFunc]:
    """Register a custom validator under *name*.

    The validator is invoked from
    :meth:`clarinet.services.record_type_service.RecordTypeService.validate_record_data`
    (and ``..._partial`` when ``run_on_partial=True``) for every RecordType
    whose ``data_validators`` list contains *name*.

    Args:
        name: Unique identifier referenced from ``RecordDef.data_validators``.
        run_on_partial: If True, also runs during prefill (partial data).
            Default False — partial data may legitimately violate full-document
            invariants and the validator would produce false positives.

    Raises:
        ValueError: If *name* is already registered (duplicate ``@record_validator``
            decorator in the same ``plan/validators.py``), or if *func* is not an
            ``async def`` coroutine function. Both surface at import time so
            misconfigs are caught at startup, not on the first submit (where
            awaiting a sync function would yield a confusing ``TypeError``).
    """

    def decorator(func: RecordValidatorFunc) -> RecordValidatorFunc:
        if not inspect.iscoroutinefunction(func):
            raise ValueError(
                f"Record validator '{name}' must be ``async def`` — "
                f"{func.__module__}.{func.__qualname__} is a regular function. "
                f"``run_record_validators`` awaits the result."
            )
        existing = _VALIDATOR_REGISTRY.get(name)
        if existing is not None:
            raise ValueError(
                f"Record validator '{name}' is already registered "
                f"(by {existing.func.__module__}.{existing.func.__qualname__}). "
                f"Choose a unique name."
            )
        _VALIDATOR_REGISTRY.register(
            name, ValidatorSpec(func=func, run_on_partial=run_on_partial), replace=False
        )
        return func

    return decorator


async def run_record_validators(
    record: Record,
    data: RecordData,
    session: AsyncSession,
    *,
    partial: bool,
) -> None:
    """Run every validator listed on ``record.record_type.data_validators``.

    Validators run sequentially (shared ``AsyncSession`` — concurrent queries
    on one connection deadlock on PostgreSQL, see ``clarinet/CLAUDE.md``).
    Errors from each validator are aggregated into a single
    :class:`RecordDataValidationError`, so the user sees every issue at once
    instead of fixing them one-by-one.

    Args:
        record: Record with ``record_type`` relation eagerly loaded.
        data: Data being submitted/updated.
        session: Async DB session for building the :class:`ValidatorContext`.
        partial: If True (prefill path), skip validators with
            ``run_on_partial=False``.

    Raises:
        RecordDataValidationError: If at least one validator reported a
            :class:`FieldError`. Field errors from all validators are merged.
    """
    names = record.record_type.data_validators or []
    if not names:
        return

    ctx = ValidatorContext.from_session(session)
    all_errors: list[FieldError] = []
    for name in names:
        spec = _VALIDATOR_REGISTRY.get(name)
        if spec is None:
            # Reconcile fail-fast should have caught unknown names at startup.
            # If we still hit this at runtime (hot config reload, race during
            # plugin development), log and skip — silently accepting the
            # submission would let a broken config bypass validation.
            logger.error(
                f"Record validator '{name}' not in registry — "
                f"reconcile should have failed first. Skipping."
            )
            continue
        if partial and not spec.run_on_partial:
            continue
        try:
            await spec.func(record, data, ctx)
        except RecordDataValidationError as exc:
            all_errors.extend(exc.errors)

    if all_errors:
        raise RecordDataValidationError(all_errors)


def load_custom_validators(folder: str | Path) -> int:
    """Load ``validators.py`` (``settings.config_validators_file``) from *folder*.

    Decorators in the loaded file auto-register into ``_VALIDATOR_REGISTRY``.
    Must be called **before** ``reconcile_config()`` in the lifespan so that
    ``reconcile_record_types`` can fail-fast on unknown validator names.

    Args:
        folder: Config root that may contain the validators file.

    Returns:
        Number of *new* validators added (0 if file not found).

    Raises:
        ConfigLoadError: If the file exists but fails to import. A
            ``ValueError`` from ``@record_validator`` (duplicate name /
            non-async function) surfaces the same way, preserved as
            ``__cause__``.
    """
    return _VALIDATOR_REGISTRY.load_from(folder)
