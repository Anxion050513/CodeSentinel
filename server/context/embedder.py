"""Code embedding — converts code snippets to vector representations.

Uses OpenAI's text-embedding-3-small model via LangChain.
The embedding model is separate from the LLM, allowing cost optimization.
"""
import logging

from server.config import settings
from server.dependencies import get_llm_factory

logger = logging.getLogger(__name__)


class CodeEmbedder:
    """Generates dense vector embeddings for code snippets.

    Used by the RAG retriever to semantically search for similar code
    patterns in the repository's codebase.
    """

    def __init__(self):
        self._embeddings = None

    @property
    def embeddings(self):
        if self._embeddings is None:
            llm_factory = get_llm_factory()
            self._embeddings = llm_factory.get_embeddings()
        return self._embeddings

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts.

        Args:
            texts: List of code snippets to embed

        Returns:
            List of embedding vectors (list of floats)
        """
        if not texts:
            return []

        try:
            vectors = await self.embeddings.aembed_documents(texts)
            return vectors
        except Exception as e:
            logger.error("Embedding failed: %s", e)
            # Return zero vectors as fallback (will match nothing in search)
            return [[0.0] * 1536 for _ in texts]

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Synchronous version of embed for use in non-async contexts."""
        if not texts:
            return []

        try:
            vectors = self.embeddings.embed_documents(texts)
            return vectors
        except Exception as e:
            logger.error("Embedding (sync) failed: %s", e)
            return [[0.0] * 1536 for _ in texts]
