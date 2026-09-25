"""
Fastapi-users configuration for session-based authentication.
Following KISS principle - minimal configuration.
"""

import hmac
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, FastAPIUsers
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    Strategy,
)
from sqlalchemy import CursorResult, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import col

from clarinet.models.auth import AccessToken
from clarinet.models.user import User
from clarinet.settings import settings
from clarinet.utils.database import get_async_session
from clarinet.utils.fastapi_users_db import SQLModelUserDatabaseAsync
from clarinet.utils.logger import logger
from clarinet.utils.session import emit_offline_if_last

# --- Failed-auth throttling (login + X-Internal-Token) ---

# ponytail: in-memory counters — per-process and reset on restart. Fine while the
# API is a single uvicorn process (the SSE bus assumes the same); move to the DB
# if that changes. TTL is fixed at import, like DatabaseStrategy._user_cache.
#
# Counts live in one-element lists so they can be bumped in place: re-assigning
# a TTLCache key restarts its TTL, which would turn the fixed window into one
# that a trickle of typos behind a shared NAT address keeps alive forever.
_auth_failures: TTLCache[str, list[int]] = TTLCache(
    maxsize=10_000, ttl=max(settings.login_lockout_minutes, 1) * 60
)
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "::ffff:127.0.0.1")  # last: dual-stack bind
_MAX_EMAIL_LENGTH = 320  # bounds the counter key: the login form accepts any string


def _account_key(email: str, client_ip: str | None) -> str:
    """Counter key for one account *as seen from one client IP*.

    Keyed by IP as well as email on purpose: an email-only lock lets any peer
    who knows an address (``admin_email`` has a well-known default) keep its
    owner out of the web UI indefinitely, and the counters are in-process, so
    nothing short of an API restart would clear it. The cost: guessing one
    account from many IPs is bounded only by the per-IP budget.
    """
    return f"acct:{client_ip or ''}:{email.lower()[:_MAX_EMAIL_LENGTH]}"


def _is_throttled(key: str, limit: int) -> bool:
    """True once ``key`` has ``limit`` failures in its window.

    The window is fixed: it opens at the key's first failure and closes
    ``login_lockout_minutes`` later, however many failures follow.
    """
    if settings.login_lockout_minutes <= 0 or limit <= 0:
        return False
    cell = _auth_failures.get(key)
    return cell is not None and bool(cell[0] >= limit)


def _record_auth_failure(key: str) -> None:
    cell = _auth_failures.get(key)
    if cell is None:
        _auth_failures[key] = [1]
    else:
        cell[0] += 1


def _forgive_auth_failure(key: str) -> None:
    """Take one counted attempt back, dropping the entry once nothing is left.

    A zero-count leftover would keep its TTL, anchoring the window at a
    *successful* login: failures landing late in it would be locked for
    seconds instead of ``login_lockout_minutes``.
    """
    cell = _auth_failures.get(key)
    if cell is None:
        return
    cell[0] -= 1
    if cell[0] <= 0:
        _auth_failures.pop(key, None)


# Minimal UserManager
class UserManager(BaseUserManager[User, UUID]):
    """Minimal user manager - only necessary methods."""

    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    def __init__(
        self, user_db: SQLModelUserDatabaseAsync[User, UUID], client_ip: str | None = None
    ) -> None:
        super().__init__(user_db)
        self.client_ip = client_ip

    async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> User | None:
        """Check credentials, throttling repeated failures per account-and-IP and per IP.

        Failures are counted for unknown emails too, so a lockout reveals nothing
        about whether the account exists. The attempted email is never logged —
        people type passwords into that field.

        Raises:
            HTTPException: 429 when the account or the client IP is locked out.
        """
        account_key = _account_key(credentials.username, self.client_ip)
        ip_key = f"ip:{self.client_ip}" if self.client_ip else None

        if _is_throttled(account_key, settings.login_max_failures_per_account) or (
            ip_key is not None and _is_throttled(ip_key, settings.login_max_failures_per_ip)
        ):
            logger.warning(
                f"Login throttled for {self.client_ip or 'unknown'}",
                extra={"reason": "login_throttled", "request_ip": self.client_ip},
            )
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts",
                headers={"Retry-After": str(settings.login_lockout_minutes * 60)},
            )

        # Count the attempt before the first await and take it back on success:
        # counting only after the password check would let a parallel burst of
        # guesses all pass the check above before any of them is recorded.
        _record_auth_failure(account_key)
        if ip_key is not None:
            _record_auth_failure(ip_key)

        try:
            user = await super().authenticate(credentials)
        except BaseException:
            # A DB outage or a cancelled request is not a wrong password: users
            # retrying through one must not come back to a locked account.
            _forgive_auth_failure(account_key)
            if ip_key is not None:
                _forgive_auth_failure(ip_key)
            raise
        if user is not None:
            _auth_failures.pop(account_key, None)
            if ip_key is not None:
                _forgive_auth_failure(ip_key)
        return user

    async def on_after_register(
        self,
        user: User,
        request: Request | None = None,
    ) -> None:
        """Called after successful user registration."""
        del request  # Unused but required by interface
        logger.info(f"User {user.id} has registered.")

    async def on_after_login(
        self,
        user: User,
        request: Request | None = None,
        response: Response | None = None,
    ) -> None:
        """Called after successful login."""
        del response  # Unused but required by interface
        user_id = str(user.id)
        logger.info(f"User {user.id} logged in.", extra={"user_id": user_id})
        if request is not None:
            # Keep only scheme://netloc — path segments can carry secrets
            # (/reset/<token>, /invite/<code>, mailto:, etc.)
            raw_referer = request.headers.get("Referer", "")
            if raw_referer:
                parts = urlsplit(raw_referer)
                safe_referer = (
                    f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
                )[:512]
            else:
                safe_referer = ""
            logger.debug(
                f"Login request metadata for user {user.id}",
                extra={
                    "user_id": user_id,
                    "user_agent": request.headers.get("User-Agent", "")[:512],
                    "referer": safe_referer,
                    "request_path": request.url.path,
                },
            )


# No longer need custom SQLModelUserDatabase - use fastapi_users_db_sqlmodel instead


async def get_user_db(
    session: AsyncSession = Depends(get_async_session),
) -> AsyncGenerator[SQLModelUserDatabaseAsync[User, UUID]]:
    """Get user database."""
    yield SQLModelUserDatabaseAsync(session, User)


async def get_user_manager(
    request: Request,
    user_db: SQLModelUserDatabaseAsync[User, UUID] = Depends(get_user_db),
) -> AsyncGenerator[UserManager]:
    """Get user manager."""
    yield UserManager(user_db, client_ip=request.client.host if request.client else None)


async def require_registration_enabled() -> None:
    """Gate for the public register route.

    Checked per request, not at mount time: the router is built at import,
    before a test (or a settings override) gets to choose the mode.
    """
    if not settings.registration_enabled:
        raise HTTPException(status_code=403, detail="Self-registration is disabled")


# Cookie transport configuration (KISS - only cookies, no tokens)
cookie_transport = CookieTransport(
    cookie_name=settings.cookie_name,
    cookie_max_age=settings.session_expire_seconds,
    cookie_httponly=True,  # Protection from XSS
    cookie_secure=not settings.debug,  # HTTPS in production
    cookie_samesite="lax",  # Protection from CSRF
)


# Enhanced database session storage strategy with lifecycle management
class DatabaseStrategy(Strategy[User, UUID]):
    """Enhanced database strategy with session lifecycle management."""

    _user_cache: ClassVar[TTLCache] = TTLCache(
        maxsize=1000,
        ttl=max(settings.session_cache_ttl_seconds, 1),
    )

    @classmethod
    def invalidate_user_cache(cls, user_id: UUID) -> None:
        """Drop every cached entry whose value.id == user_id.

        Used after role removal / deactivation to make demotions take effect
        immediately instead of after the TTL expires. Also drops the cached
        service-token user unconditionally: it is the admin row, and reloading
        it costs one query.
        """
        stale = [token for token, cached in cls._user_cache.items() if cached.id == user_id]
        for token in stale:
            cls._user_cache.pop(token, None)
        _service_user_cache.clear()

    @classmethod
    def evict_token(cls, token: str) -> None:
        """Drop a single token from the in-memory validation cache.

        Lets callers outside this class (e.g. the session-revoke endpoint)
        invalidate one session immediately without reaching into the private
        cache or deleting the DB row (which ``destroy_token`` also does).
        """
        cls._user_cache.pop(token, None)

    def __init__(self, session: AsyncSession, request: Request | None = None) -> None:
        """Initialize strategy with database session and optional request."""
        self.session = session
        self.request = request

    async def write_token(self, user: User) -> str:
        """Create new session with lifecycle management."""
        # Check concurrent session limit
        if settings.session_concurrent_limit > 0:
            await self._enforce_session_limit(user.id)

        token = str(uuid4())
        expires_at = datetime.now(UTC) + timedelta(hours=settings.session_expire_hours)

        # Extract request metadata
        user_agent = None
        ip_address = None
        if self.request is not None:
            user_agent = self.request.headers.get("User-Agent", "")[:512]
            if self.request.client is not None:
                ip_address = self.request.client.host

        access_token = AccessToken(
            token=token,
            user_id=user.id,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )

        self.session.add(access_token)
        await self.session.commit()

        logger.info(
            f"Session created for user {user.id}",
            extra={
                "user_id": str(user.id),
                "expires_at": expires_at.isoformat(),
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )

        # sse-capture: session lifecycle (AccessToken not ORM-captured). Local
        # import: capture pulls the ORM model graph; module-level would risk a cycle.
        from clarinet.services.events.capture import emit_presence

        emit_presence(user.id, True)

        return token

    async def read_token(
        self, token: str | None, user_manager: BaseUserManager[User, UUID]
    ) -> User | None:
        """Validate token with comprehensive checks and in-memory caching."""
        del user_manager  # Unused but required by interface
        if not token:
            return None

        # Check in-memory cache for recent validations
        ttl = settings.session_cache_ttl_seconds
        if ttl > 0 and token in self._user_cache:
            logger.debug(
                f"Token {token[:8]}... validated from cache",
                extra={
                    "token_preview": token[:8],
                    "cache_hit": True,
                    "request_path": self.request.url.path if self.request is not None else None,
                },
            )
            return self._user_cache[token]  # type: ignore[no-any-return]

        # Query token with expiration check
        stmt = select(AccessToken).where(
            AccessToken.token == token,  # type: ignore[arg-type]
            AccessToken.expires_at > datetime.now(UTC),  # type: ignore[arg-type]
        )
        result = await self.session.execute(stmt)
        access_token = result.scalar_one_or_none()

        if not access_token:
            request_path = self.request.url.path if self.request is not None else None
            request_ip = (
                self.request.client.host
                if self.request is not None and self.request.client is not None
                else None
            )
            logger.warning(
                "Token validation failed: token={}..., path={}, ip={}",
                token[:8],
                request_path or "N/A",
                request_ip or "N/A",
                extra={
                    "token_preview": token[:8],
                    "request_path": request_path,
                    "request_ip": request_ip,
                    "reason": "not_found_or_expired",
                },
            )
            self._user_cache.pop(token, None)
            return None

        # Optional IP validation
        if settings.session_ip_check and self.request is not None:
            request_ip = self.request.client.host if self.request.client is not None else None
            if access_token.ip_address and request_ip != access_token.ip_address:
                logger.warning(
                    f"IP mismatch for token {token[:8]}...: "
                    f"{request_ip} != {access_token.ip_address}, "
                    f"path={self.request.url.path}",
                    extra={
                        "token_preview": token[:8],
                        "request_ip": request_ip,
                        "session_ip": access_token.ip_address,
                        "request_path": self.request.url.path,
                        "reason": "ip_mismatch",
                    },
                )
                self._user_cache.pop(token, None)
                return None

        # Check idle timeout
        if settings.session_idle_timeout_minutes > 0:
            # Ensure last_accessed is timezone-aware
            last_accessed = access_token.last_accessed
            if last_accessed.tzinfo is None:
                last_accessed = last_accessed.replace(tzinfo=UTC)
            idle_duration = datetime.now(UTC) - last_accessed
            max_idle = timedelta(minutes=settings.session_idle_timeout_minutes)
            if idle_duration > max_idle:
                logger.warning(
                    f"Session idle timeout: token={token[:8]}..., "
                    f"idle_duration={idle_duration.total_seconds():.1f}s, "
                    f"max={max_idle.total_seconds():.1f}s, "
                    f"path={self.request.url.path if self.request is not None else 'N/A'}",
                    extra={
                        "token_preview": token[:8],
                        "idle_duration_seconds": idle_duration.total_seconds(),
                        "max_idle_seconds": max_idle.total_seconds(),
                        "last_accessed": last_accessed.isoformat(),
                        "request_path": self.request.url.path if self.request is not None else None,
                        "reason": "idle_timeout",
                    },
                )
                self._user_cache.pop(token, None)
                return None

        # Update last accessed and optionally refresh
        access_token.last_accessed = datetime.now(UTC)

        if settings.session_sliding_refresh:
            # Ensure expires_at is timezone-aware
            expires_at = access_token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            time_left = expires_at - datetime.now(UTC)
            total_duration = timedelta(hours=settings.session_expire_hours)

            # Refresh if less than 50% time remaining
            if time_left < total_duration / 2:
                new_expiry = datetime.now(UTC) + total_duration

                # Check absolute timeout
                if settings.session_absolute_timeout_days > 0:
                    max_age = timedelta(days=settings.session_absolute_timeout_days)
                    # Ensure created_at is timezone-aware
                    created_at = access_token.created_at
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=UTC)
                    absolute_limit = created_at + max_age
                    new_expiry = min(new_expiry, absolute_limit)

                access_token.expires_at = new_expiry
                logger.debug("Extended session {}... to {}", token[:8], new_expiry.isoformat())

        await self.session.commit()

        # Get user with roles eagerly loaded (survives expunge for caching)
        user_stmt = (
            select(User)
            .where(User.id == access_token.user_id)  # type: ignore[arg-type]
            .options(selectinload(User.roles))  # type: ignore[arg-type]
        )
        user_result = await self.session.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if not user or not user.is_active:
            logger.warning(
                f"User validation failed: user_id={access_token.user_id}, "
                f"token={token[:8]}..., "
                f"user_exists={user is not None}, "
                f"user_active={user.is_active if user else False}, "
                f"path={self.request.url.path if self.request is not None else 'N/A'}",
                extra={
                    "token_preview": token[:8],
                    "user_id": str(access_token.user_id),
                    "user_exists": user is not None,
                    "user_active": user.is_active if user else False,
                    "request_path": self.request.url.path if self.request is not None else None,
                    "reason": "user_not_found" if not user else "user_inactive",
                },
            )
            self._user_cache.pop(token, None)
            return None

        # Cache the validated user (detach from SQLAlchemy session first)
        if ttl > 0:
            self.session.expunge(user)
            self._user_cache[token] = user
            logger.debug(
                f"Token {token[:8]}... validated successfully and cached",
                extra={
                    "token_preview": token[:8],
                    "user_id": str(user.id),
                    "request_path": self.request.url.path if self.request is not None else None,
                    "cache_stored": True,
                },
            )
        else:
            logger.debug(
                f"Token {token[:8]}... validated successfully (no cache)",
                extra={
                    "token_preview": token[:8],
                    "user_id": str(user.id),
                    "request_path": self.request.url.path if self.request is not None else None,
                },
            )

        return user

    async def destroy_token(self, token: str, user: User) -> None:
        """Remove session token on logout."""
        self._user_cache.pop(token, None)
        stmt = delete(AccessToken).where(AccessToken.token == token)  # type: ignore[arg-type]
        result: CursorResult[Any] = await self.session.execute(stmt)  # type: ignore[assignment]
        await self.session.commit()

        if result.rowcount > 0:
            logger.info(
                f"Session destroyed for user {user.id}",
                extra={"user_id": str(user.id), "token_preview": token[:8] + "..."},
            )
            # sse-capture: session lifecycle (AccessToken not ORM-captured)
            await emit_offline_if_last(self.session, user.id)

    async def _enforce_session_limit(self, user_id: UUID) -> None:
        """Enforce maximum concurrent sessions per user."""
        # Count active sessions
        count_stmt = (
            select(func.count())
            .select_from(AccessToken)
            .where(
                AccessToken.user_id == user_id,  # type: ignore[arg-type]
                AccessToken.expires_at > datetime.now(UTC),  # type: ignore[arg-type]
            )
        )
        result = await self.session.execute(count_stmt)
        session_count = result.scalar() or 0

        if session_count >= settings.session_concurrent_limit:
            # Remove oldest sessions
            excess = session_count - settings.session_concurrent_limit + 1

            # Get oldest sessions
            oldest_stmt = (
                select(col(AccessToken.token))
                .where(
                    AccessToken.user_id == user_id,  # type: ignore[arg-type]
                    AccessToken.expires_at > datetime.now(UTC),  # type: ignore[arg-type]
                )
                .order_by(col(AccessToken.created_at))
                .limit(excess)
            )

            result = await self.session.execute(oldest_stmt)
            old_tokens = [row[0] for row in result]

            # Delete them
            if old_tokens:
                delete_stmt = delete(AccessToken).where(
                    AccessToken.token.in_(old_tokens)  # type: ignore[attr-defined]
                )
                await self.session.execute(delete_stmt)
                logger.info(
                    f"Removed {len(old_tokens)} old sessions for user {user_id} "
                    f"(limit: {settings.session_concurrent_limit})"
                )


def get_database_strategy(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> DatabaseStrategy:
    """Get database strategy with request context."""
    return DatabaseStrategy(session, request)


# Create authentication backend
auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_database_strategy,
)

# Create FastAPIUsers instance
fastapi_users = FastAPIUsers[User, UUID](
    get_user_manager,
    [auth_backend],
)

# --- Internal service token auth (RecordFlow, pipeline tasks) ---

_service_user_cache: TTLCache = TTLCache(maxsize=1, ttl=300)


def is_service_request(request: Request) -> bool:
    """True when the request carries a valid ``X-Internal-Token`` from an IP
    that is not locked out for guessing it (see the throttle note below).

    Single source of truth for service-token detection — used by auth
    (``_get_service_user``) and audit actor resolution (``get_audit_actor``)
    so the two cannot drift apart.
    """
    effective_token = settings.effective_service_token
    if not effective_token:
        return False

    header_token = request.headers.get("X-Internal-Token")
    if not header_token:
        return False

    # The token is derived from admin_password, so guessing it is guessing the
    # password: throttle it on the same per-IP budget as login. Loopback is
    # exempt — in-process RecordFlow and co-located workers share it, and one
    # stale worker must not lock the valid token out. A request can be counted
    # twice (auth + audit actor both land here); that only tightens the limit.
    host = request.client.host if request.client else None
    ip_key = f"ip:{host}" if host and host not in _LOOPBACK_HOSTS else None
    if ip_key and _is_throttled(ip_key, settings.login_max_failures_per_ip):
        logger.warning(
            f"Service token from {host} ignored: too many failed attempts",
            extra={"reason": "service_token_throttled"},
        )
        return False

    # Compare bytes: on str, compare_digest raises TypeError when either side is
    # non-ASCII — an unauthenticated 500 whose traceback renders the real token.
    # latin-1 restores the header's raw bytes (that is how Starlette decoded them).
    if not hmac.compare_digest(header_token.encode("latin-1", "replace"), effective_token.encode()):
        logger.warning(
            f"Invalid service token from {host or 'unknown'}",
            extra={"reason": "invalid_service_token"},
        )
        if ip_key:
            _record_auth_failure(ip_key)
        return False

    return True


async def _get_service_user(request: Request, session: AsyncSession) -> User | None:
    """Authenticate internal clients via X-Internal-Token header.

    Returns the admin User when the header matches, bypassing cookie auth
    and AccessToken creation entirely.
    """
    if not is_service_request(request):
        return None

    cache_key = "service_user"
    if cache_key in _service_user_cache:
        return _service_user_cache[cache_key]  # type: ignore[no-any-return]

    stmt = (
        select(User).where(User.email == settings.admin_email).options(selectinload(User.roles))  # type: ignore[arg-type]
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user and user.is_active:
        session.expunge(user)
        _service_user_cache[cache_key] = user
        return user

    return None


# --- Public auth dependencies (service token → cookie fallback) ---

_fu_optional_user = fastapi_users.current_user(optional=True)


async def current_active_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    fu_user: User | None = Depends(_fu_optional_user),
) -> User:
    """Return the current authenticated user.

    Checks X-Internal-Token header first (internal clients), then falls
    back to cookie-based session auth (browser users).
    """
    service_user = await _get_service_user(request, session)
    if service_user is not None:
        return service_user
    if fu_user is None or not fu_user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return fu_user


async def current_superuser(
    user: User = Depends(current_active_user),
) -> User:
    """Require an active superuser (admin or service token)."""
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Not a superuser")
    return user


async def optional_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    fu_user: User | None = Depends(_fu_optional_user),
) -> User | None:
    """Return the current user if authenticated, or None."""
    service_user = await _get_service_user(request, session)
    return service_user or fu_user
