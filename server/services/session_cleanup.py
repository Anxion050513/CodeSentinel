"""Review session cleanup and retry service.

Handles:
- Stale session detection and cleanup (sessions stuck in "reviewing" status)
- Failed session retry with exponential backoff
- Session data archival for old reviews
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.review_session import ReviewSession
from server.models.review_finding import ReviewFinding
from server.database import async_session

logger = logging.getLogger(__name__)

# Thresholds
STALE_TIMEOUT_MINUTES = 30        # Mark sessions as failed after 30 min stuck
RETRY_MAX_ATTEMPTS = 3
RETRY_BACKOFF_BASE = 60           # Start with 60s delay, then 120s, 240s
ARCHIVE_AFTER_DAYS = 90           # Delete sessions older than 90 days


class SessionCleanupService:
    """Periodic cleanup and retry for review sessions."""

    async def cleanup_stale_sessions(self):
        """Find sessions stuck in 'reviewing' status and mark them as failed."""
        async with async_session() as db:
            stale_threshold = datetime.utcnow() - timedelta(minutes=STALE_TIMEOUT_MINUTES)

            result = await db.execute(
                select(ReviewSession).where(
                    ReviewSession.status == "reviewing",
                    ReviewSession.started_at < stale_threshold,
                )
            )
            stale_sessions = result.scalars().all()

            for session in stale_sessions:
                logger.warning(
                    "Cleaning up stale session %s (PR #%d, started %s)",
                    session.id, session.pr_number,
                    session.started_at.isoformat() if session.started_at else "?",
                )
                session.status = "failed"
                session.summary = (
                    session.summary or ""
                    + " [AUTO-CLEANUP: Session timed out after "
                    f"{STALE_TIMEOUT_MINUTES} minutes]"
                )
                session.completed_at = datetime.utcnow()

            if stale_sessions:
                await db.commit()
                logger.info(
                    "Cleaned up %d stale review session(s)", len(stale_sessions)
                )
            else:
                logger.debug("No stale sessions found")

    async def retry_failed_sessions(self):
        """Retry failed review sessions with backoff."""
        async with async_session() as db:
            # Find failed sessions that can be retried
            result = await db.execute(
                select(ReviewSession).where(
                    ReviewSession.status == "failed",
                )
            )
            failed_sessions = result.scalars().all()

            retried = 0
            for session in failed_sessions:
                # Check retry count from metadata stored in stats JSON
                retry_count = (session.stats or {}).get("retry_count", 0)
                if retry_count >= RETRY_MAX_ATTEMPTS:
                    logger.debug(
                        "Session %s exceeded max retries (%d)",
                        session.id, retry_count,
                    )
                    continue

                # Check backoff: minimum delay between retries
                last_retry_str = (session.stats or {}).get("last_retry_at")
                if last_retry_str:
                    try:
                        last_retry = datetime.fromisoformat(last_retry_str)
                        backoff = RETRY_BACKOFF_BASE * (2 ** (retry_count - 1))
                        if datetime.utcnow() < last_retry + timedelta(seconds=backoff):
                            logger.debug(
                                "Session %s still in backoff (retry %d, "
                                "backoff %ds)",
                                session.id, retry_count, backoff,
                            )
                            continue
                    except (ValueError, TypeError):
                        pass

                # Retry the session
                logger.info(
                    "Retrying failed session %s (attempt %d/%d)",
                    session.id, retry_count + 1, RETRY_MAX_ATTEMPTS,
                )

                try:
                    # Update retry metadata
                    stats = session.stats or {}
                    stats["retry_count"] = retry_count + 1
                    stats["last_retry_at"] = datetime.utcnow().isoformat()
                    session.stats = stats
                    session.status = "pending"
                    session.summary = (
                        session.summary or ""
                        + f" [AUTO-RETRY: attempt {retry_count + 1}/{RETRY_MAX_ATTEMPTS}]"
                    )
                    retried += 1
                except Exception as e:
                    logger.error("Failed to queue retry for %s: %s", session.id, e)

            if retried > 0:
                await db.commit()
                logger.info("Queued %d session(s) for retry", retried)

    async def archive_old_sessions(self, older_than_days: int = ARCHIVE_AFTER_DAYS):
        """Delete sessions older than the specified threshold."""
        async with async_session() as db:
            archive_date = datetime.utcnow() - timedelta(days=older_than_days)

            # Count before deletion
            from sqlalchemy import func
            count_result = await db.execute(
                select(func.count()).select_from(ReviewSession).where(
                    ReviewSession.created_at < archive_date,
                )
            )
            count = count_result.scalar() or 0

            if count == 0:
                logger.debug("No sessions to archive")
                return

            # Delete findings for old sessions first (cascade handles this,
            # but explicit is clearer)
            old_sessions = select(ReviewSession.id).where(
                ReviewSession.created_at < archive_date,
            )

            await db.execute(
                update(ReviewFinding).where(
                    ReviewFinding.session_id.in_(old_sessions)
                ).values(is_verified=True)  # Mark as verified before deletion for audit
            )

            # Delete sessions (findings cascade)
            result = await db.execute(
                select(ReviewSession).where(
                    ReviewSession.created_at < archive_date,
                )
            )
            old = result.scalars().all()
            for session in old:
                await db.delete(session)

            await db.commit()
            logger.info("Archived %d review session(s) older than %d days", count, older_than_days)

    async def run_maintenance(self):
        """Run all cleanup tasks — call this periodically."""
        logger.info("Running session maintenance...")
        try:
            await self.cleanup_stale_sessions()
        except Exception as e:
            logger.error("Stale session cleanup failed: %s", e)

        try:
            await self.retry_failed_sessions()
        except Exception as e:
            logger.error("Session retry failed: %s", e)

        try:
            await self.archive_old_sessions()
        except Exception as e:
            logger.error("Session archival failed: %s", e)

        logger.info("Session maintenance complete")


# Singleton
cleanup_service = SessionCleanupService()