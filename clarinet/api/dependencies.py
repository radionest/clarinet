"""
Dependencies for FastAPI application with enhanced dependency injection.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, Path, Query, Request
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
from clarinet.repositories.report_repository import ReportRepository
from clarinet.repositories.series_repository import SeriesRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.repositories.user_repository import UserRepository
from clarinet.services.admin_service import AdminService
from clarinet.services.anonymization_service import AnonymizationService
from clarinet.services.dicom import DicomClient
from clarinet.services.dicom.models import DicomNode
from clarinet.services.dicomweb import DicomWebCache, DicomWebProxyService
from clarinet.services.record_service import RecordService
from clarinet.services.record_type_service import RecordTypeService
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.report_service import ReportRegistry, ReportService
from clarinet.services.slicer.service import SlicerService
from clarinet.services.study_service import StudyService
from clarinet.services.user_service import UserService
from clarinet.services.viewer import ViewerRegistry
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
    return f"{host}{settings.root_url}"


class PaginationParams:
    """Common pagination parameters."""

    def __init__(
        self,
        skip: int = Query(0, ge=0, le=100000, description="Number of items to skip"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of items to return"),
    ):
        self.skip = skip
        self.limit = limit


PaginationDep = Annotated[PaginationParams, Depends()]

# Repository factory functions


async def get_user_repository(session: SessionDep) -> UserRepository:
    """Get user repository instance."""
    return UserRepository(session)


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


async def get_user_service(user_repo: UserRepositoryDep) -> UserService:
    """Get user service instance with injected repository."""
    return UserService(user_repo)


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


async def get_record_type_service(
    record_type_repo: RecordTypeRepositoryDep,
    fd_repo: FileDefinitionRepositoryDep,
    session: SessionDep,
) -> RecordTypeService:
    """Get record type service instance with injected repositories."""
    return RecordTypeService(record_type_repo, fd_repo, session)


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
RecordTypeServiceDep = Annotated[RecordTypeService, Depends(get_record_type_service)]
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


# Report registry / service dependencies


def get_report_registry(request: Request) -> ReportRegistry:
    """Get the SQL report registry from app state.

    Returns an empty registry when state is not initialized (e.g. schema tests
    or test fixtures that bypass lifespan).
    """
    return getattr(request.app.state, "report_registry", ReportRegistry([]))


ReportRegistryDep = Annotated[ReportRegistry, Depends(get_report_registry)]


async def get_report_service(registry: ReportRegistryDep) -> ReportService:
    """Get the report service with a fresh repository instance."""
    return ReportService(registry, ReportRepository())


ReportServiceDep = Annotated[ReportService, Depends(get_report_service)]


# Viewer plugin dependencies


def get_viewer_registry(request: Request) -> ViewerRegistry:
    """Get viewer plugin registry from app state.

    Returns an empty registry when state is not initialized (e.g. schema tests).
    """
    return getattr(request.app.state, "viewer_registry", ViewerRegistry())


ViewerRegistryDep = Annotated[ViewerRegistry, Depends(get_viewer_registry)]


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


async def current_admin_user(
    user: Annotated[User, Depends(current_active_user)],
) -> User:
    """Require an active superuser OR a member of the built-in 'admin' role."""
    if user.is_superuser:
        return user
    if "admin" in get_user_role_names(user):
        return user
    raise HTTPException(status_code=403, detail="Not authorized for admin operations")


AdminUserDep = Annotated[User, Depends(current_admin_user)]


async def authorize_record_access(
    record_id: Annotated[int, Path(ge=1, le=2147483647)],
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


async def authorize_mutable_record_access(
    record: AuthorizedRecordDep,
    user: CurrentUserDep,
) -> Record:
    """Authorize mutation access: superuser, assigned user, or unassigned record."""
    if user.is_superuser:
        return record
    if record.user_id is None or record.user_id == user.id:
        return record
    raise AuthorizationError("Insufficient permissions to modify this record")


MutableRecordDep = Annotated[Record, Depends(authorize_mutable_record_access)]


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
