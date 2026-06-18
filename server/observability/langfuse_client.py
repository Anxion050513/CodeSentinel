"""LangFuse client singleton with graceful degradation.

When LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are empty or missing,
the client is disabled and all operations become no-ops.

Copied and adapted from the interview system's server/observability/langfuse_client.py.
"""
import logging

logger = logging.getLogger(__name__)


class LangFuseClientManager:
    """Singleton manager for LangFuse client.

    Returns None for all operations when disabled (no credentials configured).
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._client = None
        self._enabled = False
        self._init_client()

    def _init_client(self):
        """Attempt to initialize LangFuse client from settings."""
        try:
            from server.config import settings
        except Exception:
            return

        public_key = settings.langfuse_public_key
        secret_key = settings.langfuse_secret_key

        if not public_key or not secret_key:
            logger.debug("LangFuse disabled: no credentials configured")
            return

        try:
            import langfuse  # noqa: F401
        except ImportError:
            logger.warning(
                "LangFuse package not installed. Run: pip install langfuse"
            )
            return

        try:
            self._client = langfuse.Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=settings.langfuse_host,
            )
            self._enabled = True
            logger.info("LangFuse observability enabled (host: %s)", settings.langfuse_host)
        except Exception as e:
            logger.warning("Failed to initialize LangFuse client: %s", e)
            self._enabled = False
            self._client = None

    @property
    def client(self):
        """Return the LangFuse client, or None if disabled."""
        return self._client if self._enabled else None

    @property
    def enabled(self) -> bool:
        """Whether LangFuse tracing is active."""
        return self._enabled


def get_langfuse_client() -> LangFuseClientManager:
    """Get the LangFuse client manager singleton."""
    return LangFuseClientManager()


def is_langfuse_enabled() -> bool:
    """Quick check: is LangFuse tracing enabled?"""
    return get_langfuse_client().enabled
