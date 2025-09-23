"""
Session management utilities.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from src.models.auth import AccessToken
from src.settings import settings
from src.utils.logger import logger


async def get_user_sessions(
    session: AsyncSession, user_id: UUID, active_only: bool = True
) -> list[AccessToken]:
    """Get all sessions for a user.

    Args:
        session: Database session
        user_id: User ID
        active_only: Only return active (non-expired) sessions

    Returns:
        List of AccessToken objects
    """
    stmt = select(AccessToken).where(AccessToken.user_id == user_id)  # type:ignore[arg-type]

    if active_only:
        stmt = stmt.where(AccessToken.expires_at > datetime.now(UTC))  # type:ignore[arg-type]

    stmt = stmt.order_by(col(AccessToken.created_at).desc())

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def revoke_user_sessions(
    session: AsyncSession, user_id: UUID, except_token: str | None = None
) -> int:
    """Revoke all sessions for a user.

    Args:
        session: Database session
        user_id: User ID
        except_token: Token to exclude from revocation (current session)

    Returns:
        Number of sessions revoked
    """
    stmt = delete(AccessToken).where(AccessToken.user_id == user_id)  # type:ignore[arg-type]

    if except_token:
        stmt = stmt.where(AccessToken.token != except_token)  # type:ignore[arg-type]

    result = await session.execute(stmt)
    await session.commit()

    if result.rowcount > 0:
        logger.info(f"Revoked {result.rowcount} sessions for user {user_id}")

    return result.rowcount


async def cleanup_expired_sessions(session: AsyncSession, batch_size: int = 1000) -> int:
    """Clean up expired sessions from the database.

    Args:
        session: Database session
        batch_size: Number of sessions to delete per batch

    Returns:
        Total number of sessions deleted
    """
    deleted_total = 0

    # Count expired sessions
    count_stmt = (
        select(func.count())
        .select_from(AccessToken)
        .where(AccessToken.expires_at <= datetime.now(UTC))  # type:ignore[arg-type]
    )
    result = await session.execute(count_stmt)
    expired_count = result.scalar() or 0

    if expired_count == 0:
        logger.debug("No expired sessions to clean")
        return 0

    logger.info(f"Found {expired_count} expired sessions to clean")

    # Delete in batches
    while deleted_total < expired_count:
        # SQLite doesn't support LIMIT in DELETE, use subquery
        subquery = (
            select(col(AccessToken.token))
            .where(AccessToken.expires_at <= datetime.now(UTC))  # type:ignore[arg-type]
            .limit(batch_size)
            .subquery()
        )

        delete_stmt = delete(AccessToken).where(col(AccessToken.token).in_(select(subquery)))

        result = await session.execute(delete_stmt)
        await session.commit()

        deleted_count = result.rowcount
        deleted_total += deleted_count

        if deleted_count == 0:
            break

        logger.debug(f"Deleted batch of {deleted_count} expired sessions")

    logger.info(f"Cleanup completed: removed {deleted_total} expired sessions")

    return deleted_total


async def extend_session(
    session: AsyncSession, token: str, extend_by_hours: int | None = None
) -> AccessToken | None:
    """Extend a session's expiration time.

    Args:
        session: Database session
        token: Session token to extend
        extend_by_hours: Hours to extend by (uses settings default if None)

    Returns:
        Updated AccessToken or None if not found
    """
    if extend_by_hours is None:
        extend_by_hours = settings.session_expire_hours

    # Find the token
    stmt = select(AccessToken).where(AccessToken.token == token)  # type:ignore[arg-type]
    result = await session.execute(stmt)
    access_token = result.scalar_one_or_none()

    if not access_token:
        return None

    # Calculate new expiration
    new_expiry = datetime.now(UTC) + timedelta(hours=extend_by_hours)

    # Check absolute timeout
    if settings.session_absolute_timeout_days > 0:
        max_age = timedelta(days=settings.session_absolute_timeout_days)
        absolute_limit = access_token.created_at + max_age
        new_expiry = min(new_expiry, absolute_limit)

    # Update the token
    access_token.expires_at = new_expiry
    access_token.last_accessed = datetime.now(UTC)

    await session.commit()

    logger.debug(f"Extended session {token[:8]}... to {new_expiry.isoformat()}")

    return access_token


async def get_session_stats(session: AsyncSession) -> dict:
    """Get statistics about sessions.

    Args:
        session: Database session

    Returns:
        Dictionary with session statistics
    """
    # Total sessions
    total_stmt = select(func.count()).select_from(AccessToken)
    result = await session.execute(total_stmt)
    total = result.scalar() or 0

    # Active sessions
    active_stmt = (
        select(func.count())
        .select_from(AccessToken)
        .where(AccessToken.expires_at > datetime.now(UTC))  # type:ignore[arg-type]
    )
    result = await session.execute(active_stmt)
    active = result.scalar() or 0

    # Expired sessions
    expired = total - active

    # Sessions by age
    now = datetime.now(UTC)
    age_buckets = {
        "last_hour": now - timedelta(hours=1),
        "last_day": now - timedelta(days=1),
        "last_week": now - timedelta(weeks=1),
        "last_month": now - timedelta(days=30),
    }

    age_stats = {}
    for bucket_name, cutoff in age_buckets.items():
        stmt = (
            select(func.count())
            .select_from(AccessToken)
            .where(
                AccessToken.expires_at > now,  # type:ignore[arg-type]
                AccessToken.created_at >= cutoff,  # type:ignore[arg-type]
            )
        )
        result = await session.execute(stmt)
        count = result.scalar() or 0
        age_stats[bucket_name] = count

    # Average session duration
    avg_duration_stmt = select(
        func.avg(
            func.extract(
                "epoch",
                col(AccessToken.expires_at) - col(AccessToken.created_at),
            )
        )
    ).select_from(AccessToken)
    result = await session.execute(avg_duration_stmt)
    avg_duration_seconds = result.scalar()

    avg_duration_hours = round(avg_duration_seconds / 3600, 2) if avg_duration_seconds else 0

    return {
        "total": total,
        "active": active,
        "expired": expired,
        "by_age": age_stats,
        "average_duration_hours": avg_duration_hours,
    }


async def validate_session_token(
    session: AsyncSession, token: str, check_expiry: bool = True
) -> AccessToken | None:
    """Validate a session token.

    Args:
        session: Database session
        token: Session token to validate
        check_expiry: Whether to check if the token has expired

    Returns:
        AccessToken if valid, None otherwise
    """
    stmt = select(AccessToken).where(AccessToken.token == token)  # type:ignore[arg-type]

    if check_expiry:
        stmt = stmt.where(AccessToken.expires_at > datetime.now(UTC))  # type:ignore[arg-type]

    result = await session.execute(stmt)
    return result.scalar_one_or_none()
