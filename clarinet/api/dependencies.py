"""
Dependencies for FastAPI application with enhanced dependency injection.
"""

from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.api.auth_config import (
    current_active_user,
    current_superuser,
    optional_current_user,
)
from clarinet.exceptions import ClarinetError
from clarinet.exceptions.domain import AuthorizationError
from clarinet.models import Record, User
from clarinet.repositories.file_definition_repository import FileDefinitionRepository
from clarinet.repositories.patient_repository import PatientRepository
from clarinet.repositories.pipeline_definition_repository import PipelineDefinitionRepository
from clarinet.repositories.record_repository import RecordRepository
from clarinet.repositories.record_type_repository import RecordTypeRepository
from clarinet.repositories.series_repository import SeriesRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.repositories.user_repository import UserRepository, UserRoleRepository
from clarinet.services.admin_service import AdminService
from clarinet.services.anonymization_service import AnonymizationService
from clarinet.services.dicom import DicomClient
from clarinet.services.dicom.models import DicomNode
from clarinet.services.dicomweb import DicomWebCache, DicomWebProxyService
from clarinet.services.record_service import RecordService
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.slicer.service import SlicerService
from clarinet.services.study_service import StudyService
from clarinet.services.user_service import UserService
from clarinet.settings import settings
from clarinet.utils.database import get_async_session
from clarinet.utils.file_registry_resolver import FileRegistryEntry

# Type aliases for common dependencies
CurrentUserDep = Annotated[User, Depends(current_active_user)]
OptionalUserDep = Annotated[User | None, Depends(optional_current_user)]
SuperUserDep = Annotated[User, Depends(current_superuser)]
SessionDep = Annotated[AsyncSession, Depends(get_async_session)]


async def get_client_ip(request: Request) -> str:
    """
    Get the client's IP address.

    Args:
        request: FastAPI request object

    Returns:
        Client IP address as string
    """
    if request.client is None:
        raise ClarinetError("Cant get client IP, because request.client is None!")
    client_host = request.client.host
    return client_host


async def get_application_url(request: Request) -> str:
    """
    Get the base URL of the application.

    Args:
        request: FastAPI request object

    Returns:
        Base URL string including protocol, host, and port
    """
    host = request.url.scheme + "://" + request.url.netloc
    root_path = settings.root_url if settings.root_url != "/" else ""
    return f"{host}{root_path}"


class PaginationParams:
    """Common pagination parameters."""

    def __init__(
        self,
        skip: int = Query(0, ge=0, description="Number of items to skip"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of items to return"),
    ):
        self.skip = skip
        self.limit = limit


PaginationDep = Annotated[PaginationParams, Depends()]

# Repository factory functions


async def get_user_repository(session: SessionDep) -> UserRepository:
    """Get user repository instance."""
    return UserRepository(session)


async def get_user_role_repository(session: SessionDep) -> UserRoleRepository:
    """Get user role repository instance."""
    return UserRoleRepository(session)


async def get_study_repository(session: SessionDep) -> StudyRepository:
    """Get study repository instance."""
    return StudyRepository(session)


async def get_patient_repository(session: SessionDep) -> PatientRepository:
    """Get patient repository instance."""
    return PatientRepository(session)


async def get_series_repository(session: SessionDep) -> SeriesRepository:
    """Get series repository instance."""
    return SeriesRepository(session)


async def get_file_definition_repository(session: SessionDep) -> FileDefinitionRepository:
    """Get file definition repository instance."""
    return FileDefinitionRepository(session)


async def get_record_repository(session: SessionDep) -> RecordRepository:
    """Get record repository instance."""
    return RecordRepository(session)


async def get_record_type_repository(session: SessionDep) -> RecordTypeRepository:
    """Get record type repository instance."""
    return RecordTypeRepository(session)


async def get_pipeline_definition_repository(
    session: SessionDep,
) -> PipelineDefinitionRepository:
    """Get pipeline definition repository instance."""
    return PipelineDefinitionRepository(session)


# Repository type aliases
FileDefinitionRepositoryDep = Annotated[
    FileDefinitionRepository, Depends(get_file_definition_repository)
]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
UserRoleRepositoryDep = Annotated[UserRoleRepository, Depends(get_user_role_repository)]
StudyRepositoryDep = Annotated[StudyRepository, Depends(get_study_repository)]
PatientRepositoryDep = Annotated[PatientRepository, Depends(get_patient_repository)]
SeriesRepositoryDep = Annotated[SeriesRepository, Depends(get_series_repository)]
RecordRepositoryDep = Annotated[RecordRepository, Depends(get_record_repository)]
RecordTypeRepositoryDep = Annotated[RecordTypeRepository, Depends(get_record_type_repository)]
PipelineDefinitionRepositoryDep = Annotated[
    PipelineDefinitionRepository, Depends(get_pipeline_definition_repository)
]

# Service factory functions


def get_recordflow_engine(request: Request) -> RecordFlowEngine | None:
    """Get RecordFlow engine from app state (None when disabled)."""
    return getattr(request.app.state, "recordflow_engine", None)


async def get_user_service(
    user_repo: UserRepositoryDep, role_repo: UserRoleRepositoryDep
) -> UserService:
    """Get user service instance with injected repositories."""
    return UserService(user_repo, role_repo)


async def get_study_service(
    study_repo: StudyRepositoryDep,
    patient_repo: PatientRepositoryDep,
    series_repo: SeriesRepositoryDep,
    request: Request,
) -> StudyService:
    """Get study service instance with injected repositories."""
    return StudyService(
        study_repo, patient_repo, series_repo, engine=get_recordflow_engine(request)
    )


async def get_record_service(
    record_repo: RecordRepositoryDep,
    request: Request,
) -> RecordService:
    """Get record service instance with injected repository and engine."""
    return RecordService(record_repo, get_recordflow_engine(request))


async def get_admin_service(
    record_repo: RecordRepositoryDep,
    record_type_repo: RecordTypeRepositoryDep,
    study_repo: StudyRepositoryDep,
    patient_repo: PatientRepositoryDep,
    user_repo: UserRepositoryDep,
) -> AdminService:
    """Get admin service instance with injected repositories."""
    return AdminService(record_repo, record_type_repo, study_repo, patient_repo, user_repo)


# Slicer service factory


async def get_slicer_service() -> SlicerService:
    """Get slicer service instance."""
    return SlicerService()


# Service type aliases
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
StudyServiceDep = Annotated[StudyService, Depends(get_study_service)]
RecordServiceDep = Annotated[RecordService, Depends(get_record_service)]
AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]
SlicerServiceDep = Annotated[SlicerService, Depends(get_slicer_service)]


# DICOM dependencies


def get_dicom_client() -> DicomClient:
    """Get DICOM client instance."""
    return DicomClient(calling_aet=settings.dicom_aet, max_pdu=settings.dicom_max_pdu)


def get_pacs_node() -> DicomNode:
    """Get default PACS node configuration."""
    return DicomNode(
        aet=settings.pacs_aet,
        host=settings.pacs_host,
        port=settings.pacs_port,
    )


DicomClientDep = Annotated[DicomClient, Depends(get_dicom_client)]
PacsNodeDep = Annotated[DicomNode, Depends(get_pacs_node)]


# DICOMweb proxy dependencies


def get_dicomweb_cache(request: Request) -> DicomWebCache:
    """Get singleton DICOMweb cache from app state."""
    cache: DicomWebCache = request.app.state.dicomweb_cache
    return cache


def get_dicomweb_proxy_service(
    request: Request,
    client: DicomClientDep,
    pacs: PacsNodeDep,
) -> DicomWebProxyService:
    """Get DICOMweb proxy service instance with singleton cache."""
    cache = get_dicomweb_cache(request)
    return DicomWebProxyService(client=client, pacs=pacs, cache=cache)


DicomWebCacheDep = Annotated[DicomWebCache, Depends(get_dicomweb_cache)]
DicomWebProxyServiceDep = Annotated[DicomWebProxyService, Depends(get_dicomweb_proxy_service)]


# Anonymization dependencies


async def get_anonymization_service(
    study_repo: StudyRepositoryDep,
    patient_repo: PatientRepositoryDep,
    series_repo: SeriesRepositoryDep,
    client: DicomClientDep,
    pacs: PacsNodeDep,
) -> AnonymizationService:
    """Get anonymization service instance."""
    return AnonymizationService(study_repo, patient_repo, series_repo, client, pacs)


AnonymizationServiceDep = Annotated[AnonymizationService, Depends(get_anonymization_service)]


# Project file registry dependency


def get_project_file_registry(
    request: Request,
) -> dict[str, FileRegistryEntry] | None:
    """Get project file registry from app state."""
    return getattr(request.app.state, "project_file_registry", None)


ProjectFileRegistryDep = Annotated[
    dict[str, FileRegistryEntry] | None, Depends(get_project_file_registry)
]


def get_user_role_names(user: User) -> set[str]:
    """Extract role names from a user.

    Args:
        user: User with roles relation (eagerly loaded in auth flow).

    Returns:
        Set of role name strings.
    """
    try:
        return {role.name for role in user.roles}
    except Exception:
        return set()


async def authorize_record_access(
    record_id: int,
    user: CurrentUserDep,
    repo: RecordRepositoryDep,
) -> Record:
    """Authorize access to a record based on user roles.

    Superusers can access any record. Non-superusers can only access records
    whose RecordType.role_name matches one of their roles. Records with
    role_name=NULL are superuser-only.

    Args:
        record_id: Record ID to authorize access for.
        user: Current authenticated user.
        repo: Record repository.

    Returns:
        Record with relations loaded.

    Raises:
        RecordNotFoundError: If record doesn't exist.
        AuthorizationError: If user lacks required role.
    """
    record = await repo.get_with_relations(record_id)

    if user.is_superuser:
        return record

    role_name = record.record_type.role_name
    if role_name is None:
        raise AuthorizationError("Insufficient permissions to access this record")

    user_roles = get_user_role_names(user)
    if role_name not in user_roles:
        raise AuthorizationError("Insufficient permissions to access this record")

    return record


AuthorizedRecordDep = Annotated[Record, Depends(authorize_record_access)]


def require_mutable_config(request: Request) -> None:
    """Raise AuthorizationError if config_mode is 'python'.

    In Python config mode, RecordType mutations are disabled because
    Python files are the single source of truth.

    Args:
        request: FastAPI request to access app state.

    Raises:
        AuthorizationError: If config_mode is 'python'.
    """
    if getattr(request.app.state, "config_mode", "toml") == "python":
        raise AuthorizationError("RecordType mutations disabled in Python config mode")
