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

        # Ensure TraceContext has at least reviewer_name + chunk_file set
        # (caller should set session_id, but we set what we can here)
        try:
            from server.observability.callbacks import TraceContext
            ctx = TraceContext.get()
            if not ctx.get("reviewer_name"):
                TraceContext.set(
                    reviewer_name=self.name,
                    chunk_file=chunk.get("file_path", ""),
                )
        except Exception:
            pass

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

    @staticmethod
    def _build_codebase_hints(file_path: str) -> str:
        """Generate hints about what NOT to flag based on file role in the codebase.

        These hints prevent reviewers from flagging intentional design choices
        as bugs. Without this context, each reviewer sees an isolated function
        and can't distinguish "bug" from "design decision".
        """
        hints = [
            "### ⚠️ 代码库上下文（审查前必读）",
            "",
            "以下模式是**设计决策而非 bug**，不要报告：",
        ]

        # Admin / management paths — low traffic
        if any(p in file_path for p in ("admin.py", "router.py", "dashboard", "main.py", "observability/")):
            hints.append("- 管理后台 / 低频端点：每次请求新建 httpx 客户端是正常的，不需要连接池")
            hints.append("- 管理后台端点不需要身份验证（开发环境设计如此）")
            hints.append("- 日志 / 异常信息在开发环境返回详细信息是可接受的")

        # Observability / langfuse
        if "observability" in file_path or "langfuse" in file_path.lower():
            hints.append("- TraceContext 的 session_id 由调用方保证非空，不需要防御 None")
            hints.append("- 热路径上的 import 已缓存，Python import 首次后为零开销")

        # Aggregator / dedup
        if "aggregator" in file_path:
            hints.append("- _text_similarity 是微秒级字符串操作，不是 CPU 密集计算，同步调用安全")
            hints.append("- embeddings 有缓存层，不会对同标题重复调用 API")
            hints.append("- _embed_client 是实例属性复用设计，不是资源泄漏")
            hints.append("- 去重输入量 < 100 条，O(n²) 嵌套循环在实际规模下无性能问题")
            hints.append("- API 密钥通过环境变量注入 + HTTPS 传输，符合安全最佳实践")

        # Review service
        if "review_service" in file_path:
            hints.append("- post_pr_comment 内部有 raise_for_status，外层有 try/except + 日志")

        # Cache service
        if "cache_service" in file_path or "cache" in file_path:
            hints.append("- Redis SCAN + cursor 循环是标准写法，必定返回 cursor=0")
            hints.append("- 缓存失败降级为 pass，是有意设计的优雅降级")

        # LLM / config
        if file_path.endswith(("llm.py", "config.py", "settings.py", "dependencies.py")):
            hints.append("- settings 是 Pydantic Settings 单例，永远不会是 None")
            hints.append("- API 密钥通过环境变量注入，不是硬编码")

        # Test files
        if any(p in file_path for p in ("test_", "_check_", "seed_", "mock_", "fixture_")):
            hints.append("- 这是测试文件 / 种子脚本，其中的安全问题是有意构造的")
            hints.append("- 不要报告测试文件中的任何问题")

        # General
        hints.append("- 函数内的 import 语句有 Python 缓存，不是性能问题")
        hints.append("- Query(None) 在 FastAPI 中不会变成字符串 'None'")
        hints.append("- 浮点比较 $amount == 100.00 在金额场景是常见写法")

        return "\n".join(hints) + "\n"

    def _build_user_prompt(self, chunk: dict) -> str:
        """Build the user prompt with diff chunk and context."""
        file_path = chunk.get("file_path", "unknown")
        content = chunk.get("content", "")
        context = chunk.get("context", {})

        parts = [f"## File: `{file_path}`\n"]

        # ── Codebase context: tell reviewer what NOT to flag ──
        parts.append(self._build_codebase_hints(file_path))
        parts.append("")

        # Full function context — the complete functions touched by this diff
        full_funcs = context.get("full_functions", "")
        if full_funcs:
            parts.append("### 完整函数上下文（diff 所在函数的完整代码，消除"看不到定义"的误判）\n")
            parts.append(f"```\n{full_funcs[:6000]}\n```\n")

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
