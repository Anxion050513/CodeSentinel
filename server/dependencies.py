"""FastAPI dependency injection — reuse pattern from interview system."""
from server.config import settings
from server.ai.llm import LLMFactory


# Global singletons (lazy-init)
_llm_factory: LLMFactory | None = None


def get_llm_factory() -> LLMFactory:
    """Get or create the LLM factory singleton.

    Reuses the same factory pattern from the interview system's
    server/ai/llm.py — ChatOpenAI with auto-injected LangFuse callbacks.
    """
    global _llm_factory
    if _llm_factory is None:
        _llm_factory = LLMFactory(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            embedding_model=settings.embedding_model,
        )
    return _llm_factory
