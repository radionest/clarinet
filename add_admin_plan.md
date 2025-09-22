# Implementation Plan: Default Administrator User for Clarinet

## 1. Executive Summary

This document outlines the implementation plan for adding a default administrator user configuration to the Clarinet framework. The solution will enable automatic creation of an admin user during system initialization, with configurable credentials through environment variables or configuration files. This feature ensures immediate system access for administrators after deployment while maintaining security best practices.

### Key Benefits:
- Automated admin setup during first deployment
- Configurable credentials through environment/config
- Secure password hashing using bcrypt
- Integration with existing authentication system
- Idempotent initialization process

## 2. Current State Analysis

### 2.1 Existing Components
The Clarinet framework currently has the following relevant components:

#### User Model (`src/models/user.py`)
- String-based user ID (username)
- `is_superuser` boolean field for admin privileges
- `is_active` and `is_verified` flags
- Integration with fastapi-users

#### Authentication System
- FastAPI-users based authentication (`src/api/auth_config.py`)
- Session-based cookie authentication
- bcrypt password hashing (`src/utils/auth.py`)
- Database-backed session storage

#### Bootstrap System (`src/utils/bootstrap.py`)
- `add_default_user_roles()` function creates default roles
- Includes "admin" role in default roles list
- Database initialization during startup

#### Configuration System (`src/settings.py`)
- Pydantic-based settings with TOML support
- Environment variable overrides with `CLARINET_` prefix
- Security settings including `secret_key`

#### CLI Interface (`src/cli/main.py`)
- `db init` command for database initialization
- Calls bootstrap functions during setup

### 2.2 Gap Analysis
Currently missing:
- No default admin user creation
- No configurable admin credentials
- No mechanism to ensure admin exists
- No password validation for admin creation

## 3. Implementation Requirements

### 3.1 Functional Requirements
- Create default admin user if none exists
- Support configuration via environment variables and TOML
- Ensure idempotent creation (safe to run multiple times)
- Integrate with existing bootstrap process
- Support both CLI and API initialization

### 3.2 Non-Functional Requirements
- Secure password storage using bcrypt
- Clear logging of admin creation
- Validation of admin credentials
- Graceful handling of configuration errors
- Documentation for configuration options

### 3.3 Security Requirements
- Never log passwords in plaintext
- Require strong passwords in production
- Support disabling default admin in production
- Clear warnings about default credentials

## 4. Detailed Implementation Steps

### 4.1 Update Settings Configuration

**File:** `/home/nest/clarinet/src/settings.py`

**Changes:** Add admin user configuration fields to Settings class

```python
# Add to imports
from typing import Optional

# Add to Settings class (after line 117, before @property methods)
    # Admin user settings
    admin_username: str = "admin"
    admin_email: str = "admin@clarinet.local"
    admin_password: Optional[str] = None  # Required in production
    admin_auto_create: bool = True  # Auto-create admin on initialization
    admin_require_strong_password: bool = False  # Enforce in production
```

**Rationale:** Provides configurable admin settings with sensible defaults while requiring explicit password configuration for security.

### 4.2 Create Admin User Creation Function

**File:** `/home/nest/clarinet/src/utils/bootstrap.py`

**Changes:** Add new function for admin user creation

```python
# Add to imports
from src.utils.auth import get_password_hash
from typing import Optional

# Add after create_user_role function (after line 103)
async def create_admin_user(
    username: Optional[str] = None,
    email: Optional[str] = None,
    password: Optional[str] = None
) -> Optional[User]:
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
    from src.settings import settings

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
        existing_result = await session.execute(
            select(User).where(User.id == username)
        )
        existing_user = existing_result.scalar_one_or_none()

        if existing_user:
            logger.info(f"Admin user '{username}' already exists")

            # Ensure user has superuser privileges
            if not existing_user.is_superuser:
                existing_user.is_superuser = True
                existing_user.is_active = True
                existing_user.is_verified = True
                await session.commit()
                logger.info(f"Updated user '{username}' to superuser")

            return existing_user

        # Create new admin user
        hashed_password = get_password_hash(password)
        admin_user = User(
            id=username,
            email=email,
            hashed_password=hashed_password,
            is_active=True,
            is_superuser=True,
            is_verified=True
        )

        session.add(admin_user)
        await session.commit()
        await session.refresh(admin_user)

        # Assign admin role if it exists
        role_result = await session.execute(
            select(UserRole).where(UserRole.name == "admin")
        )
        admin_role = role_result.scalar_one_or_none()
        if admin_role:
            admin_user.roles.append(admin_role)
            await session.commit()
            logger.info(f"Assigned 'admin' role to user '{username}'")

        logger.info(
            f"Created admin user '{username}' with email '{email}'"
        )

        if settings.debug and password == "admin123":
            logger.warning(
                "⚠️  DEFAULT ADMIN CREDENTIALS IN USE!\n"
                "   Username: admin\n"
                "   Password: admin123\n"
                "   CHANGE THESE IMMEDIATELY!"
            )

        return admin_user


# Update add_default_user_roles to include admin creation
async def initialize_application_data() -> None:
    """
    Initialize application with default data including roles and admin user.

    This replaces the direct call to add_default_user_roles in CLI.
    """
    # Create default roles
    await add_default_user_roles()

    # Create admin user
    try:
        await create_admin_user()
    except ValueError as e:
        logger.error(f"Failed to create admin user: {e}")
        if not settings.debug:
            raise
```

**Rationale:** Provides secure, configurable admin creation with proper validation and logging.

### 4.3 Update CLI Database Initialization

**File:** `/home/nest/clarinet/src/cli/main.py`

**Changes:** Update database initialization to include admin creation

```python
# Update the init_database function (line 100-108)
async def init_database() -> None:
    """Initialize the database with tables and default data."""
    from src.utils.bootstrap import initialize_application_data

    logger.info("Initializing database...")
    await db_manager.create_db_and_tables_async()
    await initialize_application_data()  # Changed from add_default_user_roles
    logger.info("Database initialized successfully")
```

**Rationale:** Ensures admin user is created during CLI database initialization.

### 4.4 Add CLI Command for Admin Management

**File:** `/home/nest/clarinet/src/cli/main.py`

**Changes:** Add admin-specific CLI commands

```python
# Add after db_parser setup (after line 139)
    # admin command
    admin_parser = subparsers.add_parser("admin", help="Admin user management")
    admin_subparsers = admin_parser.add_subparsers(dest="admin_command")

    # admin create subcommand
    admin_create = admin_subparsers.add_parser("create", help="Create admin user")
    admin_create.add_argument(
        "--username", type=str, default=None, help="Admin username"
    )
    admin_create.add_argument(
        "--email", type=str, default=None, help="Admin email"
    )
    admin_create.add_argument(
        "--password", type=str, default=None,
        help="Admin password (will prompt if not provided)"
    )

    # admin reset-password subcommand
    admin_reset = admin_subparsers.add_parser(
        "reset-password", help="Reset admin password"
    )
    admin_reset.add_argument(
        "--username", type=str, default="admin",
        help="Admin username to reset"
    )

# Add handler in main() after db command handling (after line 154)
    elif args.command == "admin":
        if args.admin_command == "create":
            import getpass
            from src.utils.bootstrap import create_admin_user

            password = args.password
            if not password:
                password = getpass.getpass("Enter admin password: ")
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    logger.error("Passwords do not match")
                    sys.exit(1)

            asyncio.run(create_admin_user(
                username=args.username,
                email=args.email,
                password=password
            ))
        elif args.admin_command == "reset-password":
            import getpass
            from src.utils.admin import reset_admin_password

            password = getpass.getpass("Enter new password: ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                logger.error("Passwords do not match")
                sys.exit(1)

            asyncio.run(reset_admin_password(args.username, password))
        else:
            admin_parser.print_help()
```

**Rationale:** Provides CLI interface for admin management with secure password input.

### 4.5 Create Admin Utility Functions

**File:** `/home/nest/clarinet/src/utils/admin.py` (NEW FILE)

**Content:**
```python
"""
Administrator user management utilities.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.models.user import User
from src.utils.auth import get_password_hash
from src.utils.db_manager import db_manager
from src.utils.logger import logger


async def reset_admin_password(username: str, new_password: str) -> bool:
    """
    Reset the password for an admin user.

    Args:
        username: The admin username
        new_password: The new password to set

    Returns:
        True if password was reset, False otherwise
    """
    async with db_manager.get_async_session_context() as session:
        result = await session.execute(
            select(User).where(User.id == username)
        )
        user = result.scalar_one_or_none()

        if not user:
            logger.error(f"User '{username}' not found")
            return False

        if not user.is_superuser:
            logger.error(f"User '{username}' is not a superuser")
            return False

        user.hashed_password = get_password_hash(new_password)
        await session.commit()

        logger.info(f"Password reset for user '{username}'")
        return True


async def list_admin_users(session: AsyncSession) -> list[User]:
    """
    List all users with superuser privileges.

    Args:
        session: Database session

    Returns:
        List of admin users
    """
    result = await session.execute(
        select(User).where(User.is_superuser == True)
    )
    return list(result.scalars().all())


async def ensure_admin_exists() -> None:
    """
    Ensure at least one admin user exists in the system.

    Raises:
        RuntimeError: If no admin users exist and creation fails
    """
    async with db_manager.get_async_session_context() as session:
        admins = await list_admin_users(session)

        if not admins:
            logger.warning("No admin users found in system!")
            from src.utils.bootstrap import create_admin_user

            admin = await create_admin_user()
            if not admin:
                raise RuntimeError(
                    "No admin users exist and automatic creation failed. "
                    "System requires at least one admin user."
                )
```

**Rationale:** Provides reusable admin management functions for password reset and validation.

### 4.6 Update API Startup

**File:** `/home/nest/clarinet/src/api/app.py`

**Changes:** Add admin check on startup (location depends on existing app.py structure)

```python
# Add to startup event handler or lifespan context
from src.utils.admin import ensure_admin_exists

@app.on_event("startup")
async def startup_event():
    """Initialize application on startup."""
    # ... existing startup code ...

    # Ensure admin exists
    try:
        await ensure_admin_exists()
    except RuntimeError as e:
        logger.critical(f"Startup failed: {e}")
        # In production, you might want to exit
        if not settings.debug:
            raise
```

**Rationale:** Ensures system always has at least one admin user.

## 5. Security Considerations

### 5.1 Password Security
- Passwords are hashed using bcrypt with salt
- Never stored or logged in plaintext
- Strong password validation available for production
- Default passwords only in debug mode with warnings

### 5.2 Configuration Security
- Sensitive settings via environment variables
- CLARINET_ADMIN_PASSWORD for production deployments
- Warning messages for default credentials
- Option to disable auto-creation in production

### 5.3 Access Control
- Admin users have is_superuser=True flag
- Integration with existing role-based system
- Session-based authentication with secure cookies

## 6. Configuration Examples

### 6.1 Environment Variables (.env)
```bash
# Production configuration
CLARINET_ADMIN_USERNAME=administrator
CLARINET_ADMIN_EMAIL=admin@hospital.org
CLARINET_ADMIN_PASSWORD=SecureP@ssw0rd2024!
CLARINET_ADMIN_REQUIRE_STRONG_PASSWORD=true
CLARINET_ADMIN_AUTO_CREATE=true
CLARINET_DEBUG=false
```

### 6.2 TOML Configuration (settings.toml)
```toml
# Admin configuration
admin_username = "admin"
admin_email = "admin@clarinet.local"
# Never put passwords in config files - use environment variables
admin_auto_create = true
admin_require_strong_password = false  # Set true for production
```

### 6.3 Docker Compose
```yaml
services:
  clarinet:
    environment:
      CLARINET_ADMIN_USERNAME: admin
      CLARINET_ADMIN_EMAIL: admin@hospital.org
      CLARINET_ADMIN_PASSWORD: ${ADMIN_PASSWORD}  # From .env file
      CLARINET_ADMIN_REQUIRE_STRONG_PASSWORD: "true"
```

## 7. Testing Strategy

### 7.1 Unit Tests
```python
# tests/utils/test_admin.py
import pytest
from src.utils.bootstrap import create_admin_user
from src.utils.admin import reset_admin_password

@pytest.mark.asyncio
async def test_create_admin_user(test_session):
    """Test admin user creation."""
    admin = await create_admin_user(
        username="testadmin",
        email="test@example.com",
        password="TestPassword123!"
    )
    assert admin.is_superuser
    assert admin.is_active
    assert admin.is_verified

@pytest.mark.asyncio
async def test_duplicate_admin_creation(test_session):
    """Test idempotent admin creation."""
    admin1 = await create_admin_user(password="password")
    admin2 = await create_admin_user(password="password")
    assert admin1.id == admin2.id
```

### 7.2 Integration Tests
- Test CLI admin commands
- Test API startup with/without admin
- Test authentication with admin user
- Test role assignment to admin

### 7.3 Security Tests
- Test password hashing
- Test weak password rejection
- Test environment variable override
- Test production vs debug behavior

## 8. Migration Path

### 8.1 For New Installations
1. Set environment variables for admin credentials
2. Run `clarinet db init`
3. Admin user created automatically
4. Login with configured credentials

### 8.2 For Existing Installations
1. Update Clarinet to new version
2. Set environment variables if desired
3. Run `clarinet admin create` or let auto-creation handle it
4. Existing superusers remain unchanged

### 8.3 Database Migration
No database schema changes required - uses existing User model.

## 9. Rollback Plan

### 9.1 Disable Feature
Set in configuration:
```toml
admin_auto_create = false
```

### 9.2 Remove Admin User
```sql
-- SQL to remove default admin if needed
DELETE FROM user WHERE id = 'admin' AND is_superuser = true;
```

### 9.3 Version Rollback
1. Revert to previous Clarinet version
2. Admin functionality removed but user remains
3. Manual admin management as before

## 10. Timeline and Milestones

### Phase 1: Core Implementation (Day 1-2)
- ✅ Update settings.py with admin configuration
- ✅ Create create_admin_user function
- ✅ Update bootstrap.py
- ✅ Create admin.py utilities

### Phase 2: CLI Integration (Day 2-3)
- ✅ Add admin CLI commands
- ✅ Update db init command
- ✅ Add password reset functionality
- ✅ Test CLI commands

### Phase 3: API Integration (Day 3-4)
- ✅ Update app.py startup
- ✅ Test API initialization
- ✅ Verify authentication flow
- ✅ Test with existing users

### Phase 4: Testing & Documentation (Day 4-5)
- ✅ Write unit tests
- ✅ Write integration tests
- ✅ Update documentation
- ✅ Create migration guide

### Phase 5: Deployment (Day 5)
- ✅ Test in staging environment
- ✅ Prepare production configuration
- ✅ Deploy to production
- ✅ Monitor and verify

## Appendix A: Complete Configuration Reference

| Setting | Type | Default | Environment Variable | Description |
|---------|------|---------|---------------------|-------------|
| admin_username | str | "admin" | CLARINET_ADMIN_USERNAME | Default admin username |
| admin_email | str | "admin@clarinet.local" | CLARINET_ADMIN_EMAIL | Default admin email |
| admin_password | str | None | CLARINET_ADMIN_PASSWORD | Admin password (required) |
| admin_auto_create | bool | True | CLARINET_ADMIN_AUTO_CREATE | Auto-create on init |
| admin_require_strong_password | bool | False | CLARINET_ADMIN_REQUIRE_STRONG_PASSWORD | Enforce password policy |

## Appendix B: Security Checklist

- [ ] Never commit passwords to version control
- [ ] Use environment variables for production passwords
- [ ] Enable strong password requirements in production
- [ ] Change default admin credentials immediately
- [ ] Monitor admin login attempts
- [ ] Regular password rotation policy
- [ ] Document admin access procedures
- [ ] Implement audit logging for admin actions

## Appendix C: Troubleshooting Guide

### Problem: Admin password not set
**Solution:** Set CLARINET_ADMIN_PASSWORD environment variable

### Problem: Weak password error
**Solution:** Use password with 12+ characters, mixed case, and numbers

### Problem: Admin already exists
**Solution:** This is normal - the process is idempotent

### Problem: Cannot login as admin
**Solution:** Check is_active and is_verified flags are True

### Problem: Multiple admin users needed
**Solution:** Use `clarinet admin create` with different usernames