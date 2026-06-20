"""Context service — assembles AST + RAG + git blame context for each diff chunk."""
import logging

from server.models.repository import Repository
from server.services.github_service import GitHubService

logger = logging.getLogger(__name__)


class ContextService:
    """Builds enriched code context for each diff chunk.

    For each chunk:
    1. AST parsing — extract function/class signatures, call chains
    2. ChromaDB retrieval — semantically similar code patterns
    3. Git blame — recent modifications for context
    """

    def __init__(self, repository: Repository, github: GitHubService):
        self.repository = repository
        self.github = github
        self._parser = None
        self._retriever = None

    @property
    def ast_parser(self):
        if self._parser is None:
            from server.context.ast_parser import ASTParser
            self._parser = ASTParser()
        return self._parser

    @property
    def rag_retriever(self):
        if self._retriever is None:
            from server.context.rag_retriever import RAGRetriever
            self._retriever = RAGRetriever()
        return self._retriever

    async def build_context_for_chunks(self, chunks: list[dict]) -> list[dict]:
        """Enrich each chunk with AST + RAG + blame context.

        Only code files (.js, .py, .php, .ts, .go, .java, etc.) get full context.
        Images, empty files, and config files are skipped for speed.
        """
        # Filter to code-only chunks and limit to avoid timeouts
        CODE_EXTS = {".js", ".py", ".php", ".ts", ".tsx", ".jsx", ".go",
                     ".java", ".rb", ".rs", ".c", ".cpp", ".h", ".cs", ".swift", ".kt"}
        SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".txt",
                     ".md", ".json", ".lock", ".xml", ".wxml", ".wxss", ""}

        enriched = []

        for chunk in chunks:
            file_path = chunk.get("file_path", "")
            file_ext = file_path[file_path.rfind("."):] if "." in file_path else ""

            # Skip non-code files completely — pass through with empty context
            if file_ext.lower() in SKIP_EXTS or file_path.endswith((".wxss", ".wxml", ".json")):
                chunk["context"] = {}
                enriched.append(chunk)
                continue

            file_content = chunk.get("content", "")
            language = self._detect_language(file_path)
            context = {}

            # Only do AST + RAG + blame for code files we care about
            if file_ext.lower() in CODE_EXTS and file_content:
                # AST analysis (fast, in-process)
                try:
                    if language == "python":
                        ast_context = self.ast_parser.extract_context(file_content)
                        context["ast"] = ast_context
                except Exception:
                    pass  # AST is optional

                # Git blame (fast API call, skip RAG to save time)
                try:
                    blame_info = await self.github.get_recent_commits(
                        self.repository.owner,
                        self.repository.repo_name,
                        file_path,
                        limit=1,  # Only 1 commit for speed
                    )
                    context["recent_changes"] = [
                        {
                            "sha": c.get("sha", "")[:7],
                            "author": c.get("commit", {}).get("author", {}).get("name", ""),
                            "message": c.get("commit", {}).get("message", "").split("\n")[0],
                        }
                        for c in blame_info
                    ]
                except Exception:
                    pass  # Git blame is optional

            chunk["context"] = context
            enriched.append(chunk)

        return enriched

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension."""
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        mapping = {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "jsx": "javascript",
            "tsx": "typescript",
            "go": "go",
            "rs": "rust",
            "java": "java",
            "rb": "ruby",
            "php": "php",
            "c": "c",
            "cpp": "cpp",
            "h": "c",
            "hpp": "cpp",
            "cs": "csharp",
            "swift": "swift",
            "kt": "kotlin",
        }
        return mapping.get(ext, "unknown")
