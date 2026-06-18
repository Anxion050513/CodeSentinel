"""Base reviewer abstract class — mirroring BaseSkill from interview system."""
import json
import logging
from abc import ABC, abstractmethod

from langchain_core.messages import SystemMessage, HumanMessage

from server.ai.llm import LLMFactory
from server.ai.model_router import ModelRouter

logger = logging.getLogger(__name__)


class BaseReviewer(ABC):
    """Abstract base for all code review agents.

    Same architecture pattern as BaseSkill from the interview system.
    Each reviewer specializes in one dimension of code quality.
    """

    name: str = "base"
    display_name: str = "Base Reviewer"
    severity_weight: float = 1.0

    def __init__(self, llm_factory: LLMFactory):
        self.llm_factory = llm_factory
        self.router = ModelRouter(llm_factory)

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this reviewer."""
        ...

    async def review(self, chunk: dict) -> list[dict]:
        """Review a single diff chunk and return a list of findings.

        Args:
            chunk: Dict with keys: file_path, content, context (ast, related_code, recent_changes)

        Returns:
            List of finding dicts with keys:
                reviewer_name, severity, file_path, line_start, line_end,
                title, description, suggestion, category
        """
        system_prompt = self.get_system_prompt()
        user_prompt = self._build_user_prompt(chunk)

        chat = self.router.get_chat_model(self.name)

        try:
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
            response = await chat.ainvoke(messages)
            return self._parse_response(response.content, chunk)
        except Exception as e:
            logger.error("Reviewer '%s' failed: %s", self.name, e)
            return []

    def _build_user_prompt(self, chunk: dict) -> str:
        """Build the user prompt with diff chunk and context."""
        file_path = chunk.get("file_path", "unknown")
        content = chunk.get("content", "")
        context = chunk.get("context", {})

        parts = [f"## File: `{file_path}`\n"]

        # Code change
        parts.append("### Diff/Code Change\n")
        parts.append(f"```\n{content[:8000]}\n```\n")

        # AST context
        ast_ctx = context.get("ast")
        if ast_ctx:
            parts.append("### Code Structure (AST)\n")
            if ast_ctx.get("functions"):
                parts.append("**Functions detected:**")
                for func in ast_ctx["functions"][:5]:
                    parts.append(f"- `{func}`")
            if ast_ctx.get("classes"):
                parts.append("**Classes detected:**")
                for cls in ast_ctx["classes"][:3]:
                    parts.append(f"- `{cls}`")
            if ast_ctx.get("imports"):
                parts.append("**Imports:**")
                for imp in ast_ctx["imports"][:10]:
                    parts.append(f"- `{imp}`")
            parts.append("")

        # Related code patterns from RAG
        related = context.get("related_code", [])
        if related:
            parts.append("### Similar Code Patterns\n")
            for i, r in enumerate(related[:2], 1):
                parts.append(f"**Match {i}:** `{r.get('file_path', '?')}`\n")
                parts.append(f"```\n{r.get('content', '')[:2000]}\n```\n")

        # Recent git changes
        recent = context.get("recent_changes", [])
        if recent:
            parts.append("### Recent Modifications\n")
            for c in recent:
                parts.append(f"- `{c.get('author', '?')}`: {c.get('message', '')}")
            parts.append("")

        return "\n".join(parts)

    def _parse_response(self, response_text: str, chunk: dict) -> list[dict]:
        """Parse the LLM response into structured findings.

        Expects the LLM to return a JSON array, or a markdown list.
        """
        findings = []

        # Try JSON first
        try:
            # Find JSON array in response
            import re
            json_match = re.search(r"\[.*\]", response_text, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, list):
                    for item in parsed:
                        findings.append({
                            "reviewer_name": self.name,
                            "severity": item.get("severity", "medium"),
                            "file_path": chunk.get("file_path", ""),
                            "line_start": item.get("line", chunk.get("line_start", 0)),
                            "line_end": item.get("line_end"),
                            "title": item.get("title", "Untitled issue"),
                            "description": item.get("description", ""),
                            "suggestion": item.get("suggestion"),
                            "category": item.get("category"),
                        })
                    return findings
        except (json.JSONDecodeError, AttributeError):
            pass

        # Fallback: no findings or parse error
        if response_text and "no issue" not in response_text.lower():
            logger.debug("Could not parse findings from %s response", self.name)

        return findings
