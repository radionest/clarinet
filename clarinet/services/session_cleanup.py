"""
Background service for cleaning expired sessions.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import CursorResult, delete, func, or_, select
from sqlmodel import col

from clarinet.models.auth import AccessToken
from clarinet.settings import settings
from clarinet.utils.database import get_async_session
from clarinet.utils.logger import logger
from clarinet.utils.session import emit_offline_if_last


def _dead_session_filter() -> Any:
    """SQL predicate for sessions to evict: expired, or idle past the idle timeout.

    An idle-timed-out session is already rejected by ``read_token`` (removing it
    is safe — it cannot authenticate), and its removal is what drives the
    presence "offline" event consumed by the admin online indicator.
    """
    now = datetime.now(UTC)
    idle = settings.session_idle_timeout_minutes
    dead: Any = col(AccessToken.expires_at) <= now
    if idle > 0:
        dead = or_(dead, col(AccessToken.last_accessed) <= now - timedelta(minutes=idle))
    return dead


class SessionCleanupService:
    """Background service for cleaning expired sessions."""

    def __init__(
        self,
        cleanup_interval: int | None = None,
        batch_size: int | None = None,
    ):
        """Initialize the cleanup service.

        Args:
            cleanup_interval: Interval between cleanups in seconds
            batch_size: Number of sessions to delete per batch
        """
        self.cleanup_interval = cleanup_interval or settings.session_cleanup_interval
        self.batch_size = batch_size or settings.session_cleanup_batch_size
        self.is_running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the cleanup service."""
        if self.is_running:
            logger.warning("Session cleanup service already running")
            return

        self.is_running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info("Session cleanup service started")

    async def stop(self) -> None:
        """Stop the cleanup service."""
        self.is_running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("Session cleanup service stopped")

    async def _cleanup_loop(self) -> None:
        """Main cleanup loop."""
        while self.is_running:
            try:
                await self._perform_cleanup()
                await asyncio.sleep(self.cleanup_interval)
            except Exception as e:
                logger.error(f"Error in session cleanup: {e}")
                await asyncio.sleep(60)  # Retry after 1 minute on error

    async def _perform_cleanup(self) -> None:
        """Perform the actual cleanup."""
        async for session in get_async_session():
            try:
                # Count dead sessions (expired or idle-timed-out)
                count_stmt = (
                    select(func.count()).select_from(AccessToken).where(_dead_session_filter())
                )
                result = await session.execute(count_stmt)
                dead_count = result.scalar() or 0

                if dead_count == 0:
                    logger.debug("No dead sessions to clean")
                    return

                logger.info(f"Found {dead_count} dead sessions to clean")

                # Delete in batches to avoid locking
                deleted_total = 0
                affected_users: set[UUID] = set()
                while deleted_total < dead_count:
                    # Get tokens to delete (with details for logging)
                    tokens_query = (
                        select(AccessToken).where(_dead_session_filter()).limit(self.batch_size)
                    )
                    tokens_result = await session.execute(tokens_query)
                    tokens_to_delete = tokens_result.scalars().all()

                    if not tokens_to_delete:
                        break

                    # Log details before deletion
                    for token_obj in tokens_to_delete:
                        logger.debug(
                            f"Deleting dead session: token={token_obj.token[:8]}..., "
                            f"user_id={token_obj.user_id}, "
                            f"expires_at={token_obj.expires_at.isoformat()}, "
                            f"last_accessed={token_obj.last_accessed.isoformat()}",
                            extra={
                                "token_preview": token_obj.token[:8],
                                "user_id": str(token_obj.user_id),
                                "expires_at": token_obj.expires_at.isoformat(),
                                "last_accessed": token_obj.last_accessed.isoformat(),
                                "created_at": token_obj.created_at.isoformat(),
                                "reason": "dead",
                            },
                        )

                    # SQLite doesn't support LIMIT in DELETE, use subquery
                    token_ids = [t.token for t in tokens_to_delete]
                    affected_users.update(t.user_id for t in tokens_to_delete)
                    delete_stmt = delete(AccessToken).where(col(AccessToken.token).in_(token_ids))

                    dr: CursorResult[Any] = await session.execute(delete_stmt)  # type: ignore[assignment]
                    await session.commit()

                    deleted_count = dr.rowcount
                    deleted_total += deleted_count

                    if deleted_count == 0:
                        break

                    logger.debug(f"Deleted {deleted_count} dead sessions in batch")

                    # Small delay between batches
                    if deleted_total < dead_count:
                        await asyncio.sleep(0.1)

                logger.info(f"Cleanup completed: removed {deleted_total} sessions")

                # sse-capture: a user whose last valid session was just evicted
                # (expired or idle) goes offline.
                for uid in affected_users:
                    await emit_offline_if_last(session, uid)

                # Also clean very old sessions (older than retention days)
                if settings.session_cleanup_retention_days > 0:
                    cutoff_date = datetime.now(UTC) - timedelta(
                        days=settings.session_cleanup_retention_days
                    )

                    # Get old tokens for logging
                    old_tokens_query = select(AccessToken).where(
                        AccessToken.created_at < cutoff_date  # type:ignore[arg-type]
                    )
                    old_tokens_result = await session.execute(old_tokens_query)
                    old_tokens = old_tokens_result.scalars().all()

                    if old_tokens:
                        for token_obj in old_tokens:
                            age_days = (datetime.now(UTC) - token_obj.created_at).days
                            logger.warning(
                                f"Deleting ancient session: token={token_obj.token[:8]}..., "
                                f"user_id={token_obj.user_id}, "
                                f"age={age_days} days, "
                                f"last_accessed={token_obj.last_accessed.isoformat()}",
                                extra={
                                    "token_preview": token_obj.token[:8],
                                    "user_id": str(token_obj.user_id),
                                    "age_days": age_days,
                                    "created_at": token_obj.created_at.isoformat(),
                                    "last_accessed": token_obj.last_accessed.isoformat(),
                                    "expires_at": token_obj.expires_at.isoformat(),
                                    "reason": "ancient",
                                },
                            )

                        old_stmt = delete(AccessToken).where(
                            AccessToken.created_at < cutoff_date  # type:ignore[arg-type]
                        )
                        old_result: CursorResult[Any] = await session.execute(old_stmt)  # type: ignore[assignment]
                        await session.commit()

                        logger.info(f"Removed {old_result.rowcount} ancient sessions")

            except Exception as e:
                logger.error(f"Failed to perform cleanup: {e}")
                await session.rollback()
                raise
            finally:
                await session.close()

    async def cleanup_once(self) -> int:
        """Perform a single cleanup run.

        Returns:
            Number of sessions deleted
        """
        deleted_total = 0
        async for session in get_async_session():
            try:
                # Delete dead sessions (expired or idle-timed-out)
                delete_stmt = delete(AccessToken).where(_dead_session_filter())
                dr2: CursorResult[Any] = await session.execute(delete_stmt)  # type: ignore[assignment]
                await session.commit()
                deleted_total = dr2.rowcount

                logger.info(f"Manual cleanup: removed {deleted_total} dead sessions")

                # Also clean very old sessions
                if settings.session_cleanup_retention_days > 0:
                    cutoff_date = datetime.now(UTC) - timedelta(
                        days=settings.session_cleanup_retention_days
                    )
                    old_stmt = delete(AccessToken).where(
                        AccessToken.created_at < cutoff_date  # type:ignore[arg-type]
                    )
                    old_r: CursorResult[Any] = await session.execute(old_stmt)  # type: ignore[assignment]
                    await session.commit()

                    if old_r.rowcount > 0:
                        deleted_total += old_r.rowcount
                        logger.info(f"Removed {old_r.rowcount} ancient sessions")

            except Exception as e:
                logger.error(f"Failed to perform manual cleanup: {e}")
                await session.rollback()
                raise
            finally:
                await session.close()

        return deleted_total


# Global service instance
session_cleanup_service = SessionCleanupService()
