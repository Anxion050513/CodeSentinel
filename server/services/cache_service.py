"""Redis caching layer — caches review results, PR metadata, and session state.

Uses Redis for:
- Review result caching (avoid re-reviewing same commit)
- Session state tracking
- Rate limiting for GitHub API calls
- Temporary data with automatic expiry
"""
import json
import logging
from typing import Any, Optional

from server.config import settings

# redis is optional — cache service degrades gracefully when unavailable
try:
    import redis.asyncio as redis
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

logger = logging.getLogger(__name__)

# Default TTLs (in seconds)
TTL_REVIEW_RESULT = 3600 * 24       # 24 hours
TTL_PR_METADATA = 3600 * 2         # 2 hours
TTL_SESSION_STATE = 3600 * 24 * 7  # 7 days
TTL_RATE_LIMIT = 60                 # 1 minute


class CacheService:
    """Async Redis cache service with typed get/set operations."""

    def __init__(self):
        self._redis: Optional[redis.Redis] = None

    async def _get_client(self):
        """Get or create the Redis client. Returns None if Redis is unavailable."""
        if self._redis is not None:
            return self._redis

        if not HAS_REDIS:
            logger.debug("redis package not installed, caching disabled")
            return None

        try:
            self._redis = redis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
            await self._redis.ping()
            logger.info("Redis cache connected: %s", settings.redis_url)
            return self._redis
        except Exception as e:
            logger.warning("Redis unavailable, caching disabled: %s", e)
            self._redis = None
            return None

    # ---- Review Result Cache ----

    async def cache_review_result(
        self, commit_sha: str, repo_id: str, result: dict
    ):
        """Cache review results keyed by commit SHA."""
        client = await self._get_client()
        if client is None:
            return

        key = f"review:{repo_id}:{commit_sha}"
        try:
            await client.setex(
                key, TTL_REVIEW_RESULT, json.dumps(result, default=str)
            )
            logger.debug("Cached review result for %s", key)
        except Exception as e:
            logger.warning("Failed to cache review result: %s", e)

    async def get_cached_review_result(
        self, commit_sha: str, repo_id: str
    ) -> dict | None:
        """Get cached review result for a commit."""
        client = await self._get_client()
        if client is None:
            return None

        key = f"review:{repo_id}:{commit_sha}"
        try:
            data = await client.get(key)
            if data:
                logger.debug("Cache hit for %s", key)
                return json.loads(data)
            return None
        except Exception as e:
            logger.warning("Failed to read cache: %s", e)
            return None

    # ---- PR Metadata Cache ----

    async def cache_pr_metadata(
        self, owner: str, repo: str, pr_number: int, metadata: dict
    ):
        """Cache PR metadata from GitHub."""
        client = await self._get_client()
        if client is None:
            return

        key = f"pr:{owner}/{repo}:{pr_number}"
        try:
            await client.setex(key, TTL_PR_METADATA, json.dumps(metadata, default=str))
        except Exception as e:
            logger.warning("Failed to cache PR metadata: %s", e)

    async def get_cached_pr_metadata(
        self, owner: str, repo: str, pr_number: int
    ) -> dict | None:
        """Get cached PR metadata."""
        client = await self._get_client()
        if client is None:
            return None

        key = f"pr:{owner}/{repo}:{pr_number}"
        try:
            data = await client.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    # ---- Session State ----

    async def set_session_state(
        self, session_id: str, state: dict, ttl: int = TTL_SESSION_STATE
    ):
        """Store session progress state."""
        client = await self._get_client()
        if client is None:
            return

        key = f"session_state:{session_id}"
        try:
            await client.setex(key, ttl, json.dumps(state, default=str))
        except Exception as e:
            logger.warning("Failed to set session state: %s", e)

    async def get_session_state(self, session_id: str) -> dict | None:
        """Get session progress state."""
        client = await self._get_client()
        if client is None:
            return None

        key = f"session_state:{session_id}"
        try:
            data = await client.get(key)
            return json.loads(data) if data else None
        except Exception:
            return None

    # ---- Rate Limiting ----

    async def check_rate_limit(
        self, key: str, max_requests: int = 10, window: int = 60
    ) -> bool:
        """Check if an action is rate-limited.

        Args:
            key: Rate limit key (e.g., "github_api:repo_name")
            max_requests: Max requests allowed in the window
            window: Time window in seconds

        Returns:
            True if the request is allowed, False if rate-limited
        """
        client = await self._get_client()
        if client is None:
            return True  # Allow all when Redis is down

        rk = f"ratelimit:{key}"
        try:
            current = await client.incr(rk)
            if current == 1:
                await client.expire(rk, window)
            return current <= max_requests
        except Exception:
            return True

    # ---- Dedup (prevent duplicate webhook processing) ----

    async def mark_processed(self, event_id: str, ttl: int = 300) -> bool:
        """Mark an event as processed. Returns True if new, False if duplicate."""
        client = await self._get_client()
        if client is None:
            return True

        key = f"processed:{event_id}"
        try:
            # SET with NX (only set if not exists) for dedup
            was_set = await client.set(key, "1", ex=ttl, nx=True)
            return was_set is True or was_set == "OK" or bool(was_set)
        except Exception:
            return True  # Allow on error

    # ---- Cleanup ----

    async def invalidate_repo_cache(self, repo_id: str):
        """Invalidate all caches for a repository."""
        client = await self._get_client()
        if client is None:
            return

        try:
            pattern = f"review:{repo_id}:*"
            cursor = 0
            while True:
                cursor, keys = await client.scan(cursor, match=pattern, count=100)
                if keys:
                    await client.delete(*keys)
                if cursor == 0:
                    break
            logger.info("Cache invalidated for repo %s", repo_id)
        except Exception as e:
            logger.warning("Failed to invalidate cache: %s", e)

    async def close(self):
        """Close the Redis connection."""
        if self._redis:
            try:
                await self._redis.close()
                logger.debug("Redis connection closed")
            except Exception:
                pass
            self._redis = None


# Singleton
cache = CacheService()