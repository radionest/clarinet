"""
Simplified authentication router using fastapi-users.
"""

from fastapi import APIRouter, Depends

from src.api.auth_config import auth_backend, current_active_user, fastapi_users
from src.models.user import User, UserRead

# Use ready-made routers from fastapi-users
router = APIRouter(tags=["auth"])

# Add standard endpoints (login, logout)
router.include_router(
    fastapi_users.get_auth_router(auth_backend),
)

# User registration
# router.include_router(
#     fastapi_users.get_register_router(User, UserCreate),
# )


# Additional endpoints
@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(current_active_user)) -> User:
    """Get current user."""
    return user
