"""
Bootstrap utilities for Clarinet application initialization.

This module provides functions to initialize the application with default data,
such as user roles and record types, during startup.
"""

from collections.abc import Callable, Iterable
from pathlib import Path

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.config.reconciler import ReconcileResult, reconcile_record_types
from clarinet.exceptions.domain import ConfigLoadError, ConfigurationError
from clarinet.models import RecordType, RecordTypeCreate, User, UserRole
from clarinet.repositories.file_definition_repository import FileDefinitionRepository
from clarinet.utils.auth import get_password_hash
from clarinet.utils.config_loader import discover_config_files, load_record_config
from clarinet.utils.db_manager import db_manager
from clarinet.utils.file_link_sync import sync_file_links
from clarinet.utils.file_registry_resolver import load_project_file_registry, resolve_task_files
from clarinet.utils.logger import logger


async def add_default_user_roles() -> None:
    """Add default user roles to the database if they don't exist.

    Creates roles from three sources:
    - Built-in roles: doctor, auto, admin, expert, ordinator
    - ``settings.extra_roles``: project-specific role names
    - Keys of ``settings.role_capabilities``: any role that appears as a key
      in the capability mapping is also created automatically

    ``settings.role_capabilities`` is validated first via
    ``validate_role_capabilities``; a ``ConfigurationError`` is raised if the
    mapping references an unknown capability.  Duplicates across the three
    sources are silently ignored.
    """
    from clarinet.models.capability import validate_role_capabilities
    from clarinet.settings import settings

    # Fail fast on a typo'd capability before creating roles or hitting the DB.
    validate_role_capabilities(settings.role_capabilities)

    default_roles = ["doctor", "auto", "admin", "expert", "ordinator"]
    all_roles = list(
        dict.fromkeys(default_roles + settings.extra_roles + list(settings.role_capabilities))
    )

    async with db_manager.get_async_session_context() as session:
        for role_name in all_roles:
            try:
                await create_user_role(role_name, session=session)
                logger.info(f"Created role: {role_name}")
            except HTTPException as e:
                if e.status_code == status.HTTP_409_CONFLICT:
                    logger.info(f"Role already exists: {role_name}")
                    continue
                else:
                    raise


async def give_role_to_all_users(role_name: str) -> None:
    """
    Assign a role to all users in the database.

    Args:
        role_name: The name of the role to assign
    """
    async with db_manager.get_async_session_context() as session:
        # Eagerly load roles relationship to avoid lazy loading in async context
        users_result = await session.execute(select(User).options(selectinload(User.roles)))  # type:ignore[arg-type]
        users = users_result.scalars().all()

        role_result = await session.execute(select(UserRole).where(UserRole.name == role_name))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Role with name: {role_name} was not found!",
            )

        for user in users:
            try:
                if role not in user.roles:
                    user.roles.append(role)
                    logger.info(f"Assigned role {role_name} to user {user.id}")
            except HTTPException as e:
                if e.status_code == status.HTTP_409_CONFLICT:
                    logger.info(f"User {user.id} already has role {role_name}")
                    await session.rollback()
                    continue
                else:
                    raise

        await session.commit()


async def create_user_role(role_name: str, session: AsyncSession) -> UserRole:
    """
    Create a new user role if it doesn't exist.

    Args:
        role_name: The name of the role to create
        session: Database session

    Returns:
        The created or existing role

    Raises:
        HTTPException: If the role already exists
    """
    existing_result = await session.execute(select(UserRole).where(UserRole.name == role_name))
    existing = existing_result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role with name {role_name} already exists",
        )

    new_role = UserRole(name=role_name)
    session.add(new_role)
    await session.commit()
    await session.refresh(new_role)
    return new_role


async def create_admin_user(
    username: str | None = None,
    email: str | None = None,
    password: str | None = None,
) -> User | None:
    """
    Create a default administrator user if it doesn't exist.

    Args:
        username: Admin username (defaults to settings.admin_username)
        email: Admin email (defaults to settings.admin_email)
        password: Admin password (defaults to settings.admin_password)

    Returns:
        The created or existing admin user, None if creation disabled

    Raises:
        ValueError: If password is not configured and required
    """
    from clarinet.settings import settings

    # Check if admin creation is enabled
    if not settings.admin_auto_create:
        logger.info("Admin auto-creation is disabled")
        return None

    # Use settings defaults if not provided
    username = username or settings.admin_username
    email = email or settings.admin_email
    password = password or settings.admin_password

    # Validate password is configured
    if not password:
        if settings.debug:
            # In debug mode, use a default password with warning
            password = "admin123"
            logger.warning(
                "SECURITY WARNING: Using default admin password 'admin123'. "
                "Configure CLARINET_ADMIN_PASSWORD for production!"
            )
        else:
            raise ValueError(
                "Admin password not configured. Set CLARINET_ADMIN_PASSWORD "
                "environment variable or admin_password in settings."
            )

    # Validate password strength if required
    if settings.admin_require_strong_password:
        if len(password) < 12:
            raise ValueError("Admin password must be at least 12 characters in production")
        if not any(c.isupper() for c in password):
            raise ValueError("Admin password must contain uppercase letters")
        if not any(c.islower() for c in password):
            raise ValueError("Admin password must contain lowercase letters")
        if not any(c.isdigit() for c in password):
            raise ValueError("Admin password must contain numbers")

    async with db_manager.get_async_session_context() as session:
        # Check if admin user already exists
        existing_result = await session.execute(select(User).where(User.email == email))
        existing_user = existing_result.scalar_one_or_none()

        if existing_user:
            logger.info(f"Admin user with email '{email}' already exists")

            # Ensure user has superuser privileges
            if not existing_user.is_superuser:
                existing_user.is_superuser = True
                existing_user.is_active = True
                existing_user.is_verified = True
                await session.commit()
                logger.info(f"Updated user with email '{email}' to superuser")

            return existing_user

        # Create new admin user
        hashed_password = get_password_hash(password)
        admin_user = User(
            email=email,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=True,
            is_verified=True,
        )

        session.add(admin_user)
        await session.commit()

        # Refresh with eager loading of roles to avoid lazy loading in async context
        admin_user_result = await session.execute(
            select(User).options(selectinload(User.roles)).where(User.id == admin_user.id)  # type:ignore[arg-type]
        )
        admin_user = admin_user_result.scalar_one()

        # Assign admin role if it exists
        role_result = await session.execute(select(UserRole).where(UserRole.name == "admin"))
        admin_role = role_result.scalar_one_or_none()
        if admin_role:
            admin_user.roles.append(admin_role)
            await session.commit()
            logger.info(f"Assigned 'admin' role to user with email '{email}'")

        logger.info(f"Created admin user with email '{email}'")

        if settings.debug and password == "admin123":
            logger.warning(
                "⚠️  DEFAULT ADMIN CREDENTIALS IN USE!\n"
                "   Username: admin\n"
                "   Password: admin123\n"
                "   CHANGE THESE IMMEDIATELY!"
            )

        return admin_user


async def initialize_application_data() -> None:
    """
    Initialize application with default data including roles and admin user.

    This replaces the direct call to add_default_user_roles in CLI.
    """
    from clarinet.settings import settings

    # Create default roles
    await add_default_user_roles()

    # Create admin user
    try:
        await create_admin_user()
    except ValueError as e:
        logger.error(f"Failed to create admin user: {e}")
        if not settings.debug:
            raise


async def _upsert_record_type(record_type: RecordTypeCreate, session: AsyncSession) -> None:
    """Create a record type, logging conflicts as info.

    Args:
        record_type: Typed RecordTypeCreate object.
        session: Database session.
    """
    try:
        await add_record_type(record_type, session=session)
        logger.info(f"Created record type: {record_type.name}")
    except HTTPException as e:
        if e.status_code == status.HTTP_409_CONFLICT:
            logger.info(f"Record type already exists: {record_type.name}")
        else:
            logger.error(f"Error creating record type {record_type.name}: {e}")


def _validate_registry_refs(
    all_items: list[RecordTypeCreate],
    *,
    extract: Callable[[RecordTypeCreate], Iterable[str]],
    registered: frozenset[str],
    label: str,
    decorator: str,
    config_file: str,
    folder: str,
) -> None:
    """Fail-fast when RecordType configs reference names absent from a registry.

    Shared by the ``data_validators``, ``slicer_context_hydrators`` and
    ``x-options.source`` (schema hydrator) guards. Relies on the corresponding
    ``load_custom_*`` loader running BEFORE ``reconcile_config`` in the
    lifespan — otherwise the registry is empty and every reference is flagged
    as missing.

    Args:
        all_items: RecordType configs about to be reconciled.
        extract: Returns the registry names referenced by one RecordType — a
            flat list field (``data_validators``) or names derived from
            ``data_schema`` (``x-options.source``).
        registered: Currently registered names for this registry.
        label: Human-readable singular label for the error message.
        decorator: Decorator name to suggest in the fix hint.
        config_file: Plan file (relative to *folder*) where names are registered.
        folder: Config folder actually being reconciled (not necessarily
            ``settings.config_tasks_path`` — reconcile accepts an override).

    Raises:
        ConfigurationError: If any referenced name is not registered.
    """
    per_item: list[tuple[RecordTypeCreate, set[str]]] = [
        (item, set(extract(item))) for item in all_items
    ]
    referenced: set[str] = set()
    for _, names in per_item:
        referenced |= names
    if not referenced:
        return

    missing = referenced - registered
    if not missing:
        return

    bad_items = [
        f"  - '{item.name}' references {label}(s) {sorted(names & missing)}"
        for item, names in per_item
        if names & missing
    ]
    raise ConfigurationError(
        f"RecordType config references unregistered {label}(s): "
        f"{', '.join(sorted(missing))}.\n"
        + "\n".join(bad_items)
        + f"\nRegistered {label}s: {sorted(registered)}.\n"
        f"Register them in {folder.rstrip('/')}/{config_file} "
        f"via the @{decorator}('name') decorator."
    )


async def reconcile_config(
    folder: str | None = None,
    suffix_filter: str = "",
) -> ReconcileResult:
    """Load config and reconcile RecordTypes with the database.

    Dispatches by ``settings.config_mode``:
    - ``"toml"``: discover TOML/JSON files, resolve file refs, then reconcile.
    - ``"python"``: load Python config files, then reconcile.

    Args:
        folder: Override config folder (defaults to ``settings.config_tasks_path``).
        suffix_filter: If non-empty, only include configs whose stem
            contains this substring.

    Returns:
        ReconcileResult with counts per category.
    """
    from clarinet.settings import settings

    folder = folder or settings.config_tasks_path
    all_items: list[RecordTypeCreate] = []

    if settings.config_mode == "python":
        from clarinet.config.python_loader import load_python_config

        all_items = await load_python_config(Path(folder))
    else:
        # TOML mode — use existing loaders
        config_files = discover_config_files(folder, suffix_filter)
        if not config_files:
            logger.warning(f"No record type configs found in {folder}")
            return ReconcileResult()

        logger.info(f"Found record type configs: {[p.stem for p in config_files]}")
        project_registry = await load_project_file_registry(folder)

        for config_path in config_files:
            try:
                props = await load_record_config(config_path)
                if props is None:
                    continue
                props = resolve_task_files(props, project_registry)
                all_items.append(RecordTypeCreate(**props))
            except ConfigLoadError:
                raise
            except ValidationError as e:
                # A record-type config that violates a model invariant (e.g.
                # shared_editing + unique_per_user) must abort startup, not be
                # swallowed by the lenient handler below — otherwise the type is
                # silently dropped and later references 404 at runtime.
                raise ConfigurationError(
                    f"Invalid record type config '{config_path.name}': {e}"
                ) from e
            except Exception as e:
                logger.error(f"Error processing record type {config_path.name}: {e}")

    async with db_manager.get_async_session_context() as session:
        # Validate that all referenced role_names exist in the DB
        referenced_roles = {item.role_name for item in all_items if item.role_name is not None}
        if referenced_roles:
            all_roles_result = await session.execute(select(UserRole.name))
            all_db_roles = set(all_roles_result.scalars().all())
            missing = referenced_roles - all_db_roles
            if missing:
                bad_items = [
                    f"  - '{item.name}' references role '{item.role_name}'"
                    for item in all_items
                    if item.role_name in missing
                ]
                raise ConfigurationError(
                    f"RecordType config references undefined role(s): "
                    f"{', '.join(sorted(missing))}.\n"
                    + "\n".join(bad_items)
                    + f"\nAvailable roles: {sorted(all_db_roles)}.\n"
                    f"Add missing roles to CLARINET_EXTRA_ROLES or fix the config."
                )

        # Validate that referenced viewer names are actually configured. A typo
        # (e.g. ["ohiff"]) would otherwise pass a non-empty allowlist that
        # matches no configured viewer, silently hiding every viewer button on
        # the record page. Fail fast at startup instead. (Built-in adapter names
        # — ohif/radiant/weasis — plus any custom TemplateAdapter live in
        # settings.viewers, so that dict is the source of truth.)
        referenced_viewers: set[str] = set()
        for item in all_items:
            referenced_viewers.update(item.allowed_viewers or [])
        if referenced_viewers:
            configured_viewers = set(settings.viewers)
            missing_viewers = referenced_viewers - configured_viewers
            if missing_viewers:
                bad_items = [
                    f"  - '{item.name}' allows viewer(s) "
                    f"{[n for n in (item.allowed_viewers or []) if n in missing_viewers]}"
                    for item in all_items
                    if any(n in missing_viewers for n in (item.allowed_viewers or []))
                ]
                raise ConfigurationError(
                    f"RecordType config references unconfigured viewer(s): "
                    f"{', '.join(sorted(missing_viewers))}.\n"
                    + "\n".join(bad_items)
                    + f"\nConfigured viewers: {sorted(configured_viewers)}.\n"
                    f"Enable them via [viewers.<name>] in settings.toml."
                )

        # Validate decorator-registry references (data_validators,
        # slicer_context_hydrators) and x-options.source names inside data_schema
        # (schema hydrators). A typo used to surface only at runtime — e.g. when
        # the doctor opened the record in Slicer, or as a render-time warning that
        # left the field raw.
        from clarinet.services.record_data_validation import (
            get_registered_validator_names,
        )
        from clarinet.services.schema_hydration import (
            collect_x_options_sources,
            get_registered_schema_hydrator_names,
        )
        from clarinet.services.slicer.context_hydration import (
            get_registered_slicer_hydrator_names,
        )

        _validate_registry_refs(
            all_items,
            extract=lambda it: it.data_validators or [],
            registered=get_registered_validator_names(),
            label="data validator",
            decorator="record_validator",
            config_file=settings.config_validators_file,
            folder=folder,
        )
        _validate_registry_refs(
            all_items,
            extract=lambda it: it.slicer_context_hydrators or [],
            registered=get_registered_slicer_hydrator_names(),
            label="slicer context hydrator",
            decorator="slicer_context_hydrator",
            config_file=settings.config_context_hydrators_file,
            folder=folder,
        )
        _validate_registry_refs(
            all_items,
            extract=lambda it: collect_x_options_sources(it.data_schema or {}),
            registered=get_registered_schema_hydrator_names(),
            label="schema hydrator",
            decorator="schema_hydrator",
            config_file=settings.config_schema_hydrators_file,
            folder=folder,
        )

        result = await reconcile_record_types(
            all_items,
            session,
            delete_orphans=settings.config_delete_orphans,
        )

    return result


async def add_record_type(record_type: RecordTypeCreate, session: AsyncSession) -> RecordType:
    """Add a new record type to the database with file links.

    Args:
        record_type: The record type to add
        session: Database session

    Returns:
        The created record type

    Raises:
        HTTPException: If the record type already exists
    """
    # Check if record type with this name already exists
    existing_result = await session.execute(
        select(RecordType).where(RecordType.name == record_type.name)
    )
    existing = existing_result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Record type with name {record_type.name} already exists",
        )

    # Extract file_registry before creating the ORM object
    file_defs = record_type.file_registry or []

    # Create RecordType without file_registry (it's M2M, not a column)
    create_data = record_type.model_dump(exclude={"file_registry"})
    new_record_type = RecordType(**create_data)
    new_record_type.file_links = []
    session.add(new_record_type)
    await session.flush()

    # Create file links
    if file_defs:
        fd_repo = FileDefinitionRepository(session)
        await sync_file_links(new_record_type, file_defs, fd_repo, session)

    await session.commit()
    await session.refresh(new_record_type)

    return new_record_type
