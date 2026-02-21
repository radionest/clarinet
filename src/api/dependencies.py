"""
Dependencies for FastAPI application with enhanced dependency injection.
"""

from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth_config import (
    current_active_user,
    current_superuser,
    optional_current_user,
)
from src.exceptions import ClarinetError
from src.models import User
from src.repositories.patient_repository import PatientRepository
from src.repositories.record_repository import RecordRepository
from src.repositories.record_type_repository import RecordTypeRepository
from src.repositories.series_repository import SeriesRepository
from src.repositories.study_repository import StudyRepository
from src.repositories.user_repository import UserRepository, UserRoleRepository
from src.services.study_service import StudyService
from src.services.user_service import UserService
from src.settings import settings
from src.utils.database import get_async_session

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


async def get_record_repository(session: SessionDep) -> RecordRepository:
    """Get record repository instance."""
    return RecordRepository(session)


async def get_record_type_repository(session: SessionDep) -> RecordTypeRepository:
    """Get record type repository instance."""
    return RecordTypeRepository(session)


# Repository type aliases
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
UserRoleRepositoryDep = Annotated[UserRoleRepository, Depends(get_user_role_repository)]
StudyRepositoryDep = Annotated[StudyRepository, Depends(get_study_repository)]
PatientRepositoryDep = Annotated[PatientRepository, Depends(get_patient_repository)]
SeriesRepositoryDep = Annotated[SeriesRepository, Depends(get_series_repository)]
RecordRepositoryDep = Annotated[RecordRepository, Depends(get_record_repository)]
RecordTypeRepositoryDep = Annotated[RecordTypeRepository, Depends(get_record_type_repository)]

# Service factory functions


async def get_user_service(
    user_repo: UserRepositoryDep, role_repo: UserRoleRepositoryDep
) -> UserService:
    """Get user service instance with injected repositories."""
    return UserService(user_repo, role_repo)


async def get_study_service(
    study_repo: StudyRepositoryDep,
    patient_repo: PatientRepositoryDep,
    series_repo: SeriesRepositoryDep,
) -> StudyService:
    """Get study service instance with injected repositories."""
    return StudyService(study_repo, patient_repo, series_repo)


# Service type aliases
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
StudyServiceDep = Annotated[StudyService, Depends(get_study_service)]
