"""RAG retriever — semantic search for similar code patterns using ChromaDB."""
import logging
import os

import chromadb
from chromadb.config import Settings as ChromaSettings

from server.config import settings

logger = logging.getLogger(__name__)


class RAGRetriever:
    """Searches the codebase for semantically similar code snippets.

    Uses ChromaDB as the vector store. Code snippets are indexed by
    repository, file path, and function/class. Retrieval returns the
    most similar existing code patterns, which provide context for
    the reviewer to understand the codebase's conventions and patterns.
    """

    def __init__(self):
        self._client = None
        self._embedder = None

    @property
    def client(self) -> chromadb.Client:
        if self._client is None:
            persist_dir = settings.chroma_persist_dir
            os.makedirs(persist_dir, exist_ok=True)
            self._client = chromadb.Client(ChromaSettings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=persist_dir,
                anonymized_telemetry=False,
            ))
            logger.debug("ChromaDB initialized at %s", persist_dir)
        return self._client

    @property
    def embedder(self):
        if self._embedder is None:
            from server.context.embedder import CodeEmbedder
            self._embedder = CodeEmbedder()
        return self._embedder

    def _get_collection(self, repo_id: str) -> chromadb.Collection:
        """Get or create a ChromaDB collection for a repository."""
        collection_name = f"code_{repo_id.replace('-', '_')}"
        try:
            return self.client.get_collection(collection_name)
        except Exception:
            return self.client.create_collection(
                name=collection_name,
                metadata={"repo_id": repo_id},
            )

    def search(
        self,
        query: str,
        repo_id: str,
        top_k: int = 3,
    ) -> list[dict]:
        """Search for code snippets semantically similar to the query.

        Args:
            query: The code snippet to search for (e.g., a changed function)
            repo_id: The repository ID to scope the search to
            top_k: Number of results to return

        Returns:
            List of dicts with: file_path, content, distance, metadata
        """
        try:
            collection = self._get_collection(repo_id)
            if collection.count() == 0:
                return []

            # Generate embedding for the query
            from server.context.embedder import CodeEmbedder
            embedder = CodeEmbedder()
            query_embedding = embedder.embed_sync([query])

            if not query_embedding or not query_embedding[0]:
                return []

            results = collection.query(
                query_embeddings=query_embedding,
                n_results=min(top_k, collection.count()),
                include=["documents", "metadatas", "distances"],
            )

            if not results or not results["ids"] or not results["ids"][0]:
                return []

            formatted = []
            for i, doc_id in enumerate(results["ids"][0]):
                formatted.append({
                    "id": doc_id,
                    "content": results["documents"][0][i] if results["documents"] else "",
                    "file_path": results["metadatas"][0][i].get("file_path", "") if results["metadatas"] else "",
                    "distance": results["distances"][0][i] if results["distances"] else 0.0,
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                })

            return formatted

        except Exception as e:
            logger.warning("RAG search failed: %s", e)
            return []

    def index_file(
        self,
        repo_id: str,
        file_path: str,
        content: str,
        language: str = "python",
    ):
        """Index a single file into the vector store.

        The file is split into logical segments (functions, classes)
        and each segment is embedded separately.
        """
        collection = self._get_collection(repo_id)

        try:
            from server.context.embedder import CodeEmbedder
            embedder = CodeEmbedder()

            # Split into logical segments (functions/classes)
            segments = self._segment_code(content, language)

            if not segments:
                # Index the whole file
                embedding = embedder.embed_sync([content[:5000]])
                if embedding and embedding[0]:
                    collection.add(
                        documents=[content[:5000]],
                        metadatas=[{"file_path": file_path, "segment": "full"}],
                        ids=[f"{file_path}:full"],
                        embeddings=embedding,
                    )
            else:
                for seg in segments:
                    seg_id = f"{file_path}:{seg['name']}"
                    embedding = embedder.embed_sync([seg["content"][:5000]])
                    if embedding and embedding[0]:
                        collection.add(
                            documents=[seg["content"][:5000]],
                            metadatas=[{
                                "file_path": file_path,
                                "segment": seg["name"],
                                "type": seg.get("type", "function"),
                            }],
                            ids=[seg_id],
                            embeddings=embedding,
                        )

            logger.debug("Indexed %s (%d segments)", file_path, len(segments))

        except Exception as e:
            logger.warning("Failed to index %s: %s", file_path, e)

    def _segment_code(self, content: str, language: str) -> list[dict]:
        """Split source code into logical segments (functions/classes)."""
        import re

        segments = []

        if language == "python":
            # Python: split on def/class boundaries
            pattern = re.compile(
                r'^(\s*)(?:async\s+)?(def|class)\s+(\w+)',
                re.MULTILINE,
            )
            matches = list(pattern.finditer(content))
            for i, m in enumerate(matches):
                start = m.start()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                seg_content = content[start:end].strip()
                seg_type = m.group(2)  # def or class
                seg_name = m.group(3)
                if len(seg_content) > 10:
                    segments.append({
                        "name": seg_name,
                        "type": seg_type,
                        "content": seg_content,
                    })

        return segments

    def clear_repo(self, repo_id: str):
        """Clear all indexed code for a repository."""
        try:
            collection_name = f"code_{repo_id.replace('-', '_')}"
            self.client.delete_collection(collection_name)
            logger.info("Cleared collection for repo %s", repo_id)
        except Exception as e:
            logger.warning("Failed to clear collection: %s", e)
