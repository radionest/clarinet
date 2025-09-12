"""
Simplified dependencies using fastapi-users.
"""

from fastapi import Query, Request

from src.api.auth_config import (
    current_active_user,
    current_superuser,
    optional_current_user,
)
from src.exceptions import ClarinetError
from src.settings import settings

# Export for use in other modules
get_current_user_async = current_active_user
get_current_user_cookie_async = current_active_user  # Alias for compatibility
get_current_superuser_async = current_superuser
get_optional_user_async = optional_current_user

# Compatibility aliases
get_current_user = current_active_user


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


async def common_parameters(
    skip: int = Query(0, ge=0, description="Number of items to skip"),
    limit: int | None = Query(None, ge=1, description="Maximum number of items to return"),
) -> dict[str, int | None]:
    """
    Get common query parameters for pagination.

    Args:
        skip: Number of items to skip
        limit: Maximum number of items to return

    Returns:
        Dictionary with skip and limit parameters
    """
    return {"skip": skip, "limit": limit}
