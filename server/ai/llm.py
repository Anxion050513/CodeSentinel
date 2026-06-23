"""LLM Factory for OpenAI-compatible APIs.

Reused and adapted from the interview system's server/ai/llm.py.
Auto-injects LangFuse callbacks when observability is enabled.
"""
import logging

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

logger = logging.getLogger(__name__)


class LLMFactory:
    """Factory for creating LLM and embedding model instances."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "gpt-4o",
        embedding_model: str = "text-embedding-3-small",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.embedding_model = embedding_model

    def get_chat_model(
        self,
        temperature: float = 0.3,
        streaming: bool = False,
        model: str | None = None,
        callbacks: list | None = None,
    ) -> ChatOpenAI:
        """Get a configured ChatOpenAI instance.

        Automatically injects LangFuse callback from TraceContext when
        observability is enabled. Pass additional callbacks via `callbacks`.
        """
        all_callbacks = list(callbacks or [])

        # Auto-inject LangFuse callback from current trace context
        try:
            from server.observability.callbacks import get_langfuse_callback
            lf_cb = get_langfuse_callback()
            if lf_cb:
                all_callbacks.append(lf_cb)
                logger.debug("LangFuse callback injected (total callbacks: %d)", len(all_callbacks))
        except Exception:
            pass  # observability module not available — no tracing

        kwargs: dict = dict(
            api_key=self.api_key,
            base_url=self.base_url,
            model=model or self.model,
            temperature=temperature,
            streaming=streaming,
        )
        if all_callbacks:
            kwargs["callbacks"] = all_callbacks
        return ChatOpenAI(**kwargs)

    def get_embeddings(self) -> OpenAIEmbeddings:
        """Get a configured OpenAIEmbeddings instance.

        Uses dedicated embedding credentials (EMBEDDING_API_KEY / EMBEDDING_BASE_URL),
        NOT the LLM credentials — embeddings may use a different provider (e.g. DashScope).
        """
        from server.config import settings
        return OpenAIEmbeddings(
            api_key=settings.embedding_api_key or self.api_key,
            base_url=settings.embedding_base_url or self.base_url,
            model=settings.embedding_model or self.embedding_model,
        )
