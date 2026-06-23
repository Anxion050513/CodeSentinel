"""Context service — full-function context + AST + blame."""
import logging

from server.models.repository import Repository
from server.services.github_service import GitHubService

logger = logging.getLogger(__name__)


class ContextService:
    """Builds enriched code context for each diff chunk.

    For each chunk in a code file:
    1. Fetch full file from GitHub → extract complete function bodies for diff lines
    2. AST parsing — function/class signatures, call chains
    3. Git blame — recent modifications for context

    Full function context eliminates "can't see the definition" false positives.
    One API call per unique file (cached per commit+path).
    """

    def __init__(self, repository: Repository, github: GitHubService):
        self.repository = repository
        self.github = github
        self._parser = None
        self._retriever = None
        self._full_file_cache: dict[str, str] = {}  # commit:path → content

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

    async def build_context_for_chunks(
        self, chunks: list[dict], commit_ref: str = ""
    ) -> list[dict]:
        """Enrich each chunk with full function context + AST + blame.

        commit_ref: commit SHA or branch name for fetching full file content.
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
                # Full function context: fetch complete file, extract touched functions
                try:
                    full_funcs = await self._get_function_context(
                        file_path, language,
                        chunk.get("line_start") or 0,
                        chunk.get("line_end") or 0,
                        commit_ref,
                    )
                    if full_funcs:
                        context["full_functions"] = full_funcs
                except Exception:
                    pass

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

    async def _get_function_context(
        self, file_path: str, language: str, line_start: int, line_end: int,
        commit_ref: str = "",
    ) -> str:
        """Fetch full file from GitHub at commit_ref, extract functions containing
        the changed lines. Cached per commit+path — one API call per file.
        Returns empty string for non-Python or on failure (graceful degradation).
        """
        if language != "python" or line_start <= 0 or not commit_ref:
            return ""

        cache_key = f"{commit_ref}:{file_path}"
        if cache_key in self._full_file_cache:
            full_content = self._full_file_cache[cache_key]
        else:
            try:
                full_content = await self.github.get_file_content(
                    self.repository.owner,
                    self.repository.repo_name,
                    file_path,
                    ref=commit_ref,
                )
                self._full_file_cache[cache_key] = full_content or ""
            except Exception:
                return ""

        if not full_content:
            return ""

        lines = list(range(line_start, max(line_start, line_end) + 1))
        return self.ast_parser.find_functions_for_lines(full_content, lines, language)

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
