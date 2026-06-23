"""LangChain callback integration with async-safe trace context.

Uses a custom BaseCallbackHandler (from langchain_core) that translates
LangChain LLM events into langfuse generations via the native SDK.

Copied and adapted from the interview system's server/observability/callbacks.py.
"""
import contextvars
import logging
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger(__name__)

# === Async-safe trace context via contextvars ===

_trace_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_session_id", default=""
)
_trace_reviewer_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_reviewer_name", default=""
)
_trace_chunk_file: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_chunk_file", default=""
)
_trace_phase: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_phase", default="unknown"
)
_trace_repo: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_repo", default=""
)
_trace_pr_number: contextvars.ContextVar[int] = contextvars.ContextVar(
    "trace_pr_number", default=0
)


class TraceContext:
    """Async-safe trace metadata storage backed by contextvars.

    Usage:
        TraceContext.set(session_id="abc", reviewer_name="security", phase="review")
        try:
            ...  # LLM calls here automatically pick up this context
        finally:
            TraceContext.clear()
    """

    @classmethod
    def set(
        cls,
        *,
        session_id: str = "",
        reviewer_name: str = "",
        chunk_file: str = "",
        phase: str = "",
        repo: str = "",
        pr_number: int = 0,
    ):
        """Set trace context for the current async task."""
        if session_id:
            _trace_session_id.set(session_id)
        if reviewer_name:
            _trace_reviewer_name.set(reviewer_name)
        if chunk_file:
            _trace_chunk_file.set(chunk_file)
        if phase:
            _trace_phase.set(phase)
        if repo:
            _trace_repo.set(repo)
        if pr_number:
            _trace_pr_number.set(pr_number)

    @classmethod
    def get(cls) -> dict:
        """Get current trace context as a dict."""
        return {
            "session_id": _trace_session_id.get(),
            "reviewer_name": _trace_reviewer_name.get(),
            "chunk_file": _trace_chunk_file.get(),
            "phase": _trace_phase.get(),
            "repo": _trace_repo.get(),
            "pr_number": _trace_pr_number.get(),
        }

    @classmethod
    def clear(cls):
        """Reset all trace context fields to defaults."""
        _trace_session_id.set("")
        _trace_reviewer_name.set("")
        _trace_chunk_file.set("")
        _trace_phase.set("unknown")
        _trace_repo.set("")
        _trace_pr_number.set(0)


# === LangFuse Tracer (custom BaseCallbackHandler) ===


class LangFuseTracer(BaseCallbackHandler):
    """Translates LangChain LLM callbacks → langfuse generations.

    Each LLM call becomes a langfuse Generation. All generations
    within the same session share the same trace_id (= session_id).
    """

    def __init__(self):
        self._pending: dict[UUID, object] = {}  # run_id → StatefulGenerationClient

    @property
    def client(self):
        """Lazy-access the langfuse client (may not be initialized at import time)."""
        from server.observability.langfuse_client import get_langfuse_client
        mgr = get_langfuse_client()
        return mgr.client if mgr.enabled else None

    @property
    def ctx(self) -> dict:
        """Current trace context."""
        return TraceContext.get()

    # === LangChain callback interface ===

    def _create_generation(self, run_id: UUID, model_name: str, input_data):
        """Create a langfuse generation for the current trace context.

        Uses langfuse v4.x API: start_observation() with trace_context.
        The trace is auto-created on first observation referencing a given trace_id.
        """
        cl = self.client
        if cl is None:
            return

        ctx = self.ctx
        session_id = ctx.get("session_id", "")
        phase = ctx.get("phase", "unknown")
        reviewer = ctx.get("reviewer_name", "")
        chunk_file = ctx.get("chunk_file", "")
        repo = ctx.get("repo", "")
        pr_number = ctx.get("pr_number", 0)

        # Build readable observation name: "repo#PR — security" (trace name = first gen's name)
        if repo and pr_number:
            gen_name = f"{repo}#{pr_number} — {reviewer}" if reviewer else f"{repo}#{pr_number} — {phase}"
        elif reviewer:
            gen_name = f"{phase}-{reviewer}"
        else:
            gen_name = phase

        metadata = {
            "session_id": session_id,
            "reviewer_name": reviewer,
            "chunk_file": chunk_file,
            "phase": phase,
            "repo": repo,
            "pr_number": pr_number,
        }
        tags = [t for t in [phase, reviewer, repo] if t]

        try:
            # langfuse v4.x: start_observation() creates trace+generation in one call
            # trace_id must be 32 lowercase hex chars (UUID without dashes)
            from langfuse.types import TraceContext

            clean_trace_id = session_id.replace("-", "") if session_id else ""
            gen = cl.start_observation(
                trace_context=TraceContext(trace_id=clean_trace_id) if clean_trace_id else None,
                name=gen_name,
                as_type="generation",
                model=model_name,
                input=input_data,
                metadata=metadata,
            )
            self._pending[run_id] = gen
            logger.info(
                "LangFuse gen created: name=%s model=%s session=%s",
                gen_name, model_name, session_id[:8] if session_id else "-",
            )
        except Exception as e:
            logger.warning("LangFuse create_generation failed: %s", e, exc_info=True)

    def _end_generation(self, run_id: UUID, output: str, tok: dict | None = None, error: str | None = None):
        """Update and end a pending generation, then flush to langfuse (v4.x API)."""
        gen = self._pending.pop(run_id, None)
        if gen is None:
            return
        try:
            usage = {}
            if tok:
                usage["input"] = tok.get("prompt_tokens", 0) or 0
                usage["output"] = tok.get("completion_tokens", 0) or 0
                if tok.get("total_tokens"):
                    usage["total"] = tok["total_tokens"]

            if error:
                gen.update(output=output or "", status_message=error[:500], level="ERROR")
            else:
                gen.update(output=output or "", usage_details=usage if usage else None)
            gen.end()
            logger.info(
                "LangFuse gen ended: run_id=%s output_len=%d",
                run_id, len(output) if output else 0,
            )

            # Flush immediately so data appears in dashboard without delay
            cl = self.client
            if cl:
                try:
                    cl.flush()
                    logger.debug("LangFuse flush OK for run_id=%s", run_id)
                except Exception as flush_err:
                    logger.warning("LangFuse flush FAILED for run_id=%s: %s", run_id, flush_err)
        except Exception as e:
            logger.warning("LangFuse end_generation failed: %s", e, exc_info=True)

    # ---- Chat model callbacks (langchain 1.x uses these for ChatOpenAI) ----

    def on_chat_model_start(
        self,
        serialized: dict,
        messages: list,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
        **kwargs,
    ):
        """Called before every ChatOpenAI call (langchain 1.x)."""
        llm_kwargs = serialized.get("kwargs", {})
        model_name = llm_kwargs.get("model", llm_kwargs.get("model_name", "unknown"))
        # Convert messages to a serializable form
        input_data = []
        for msg_list in messages:
            for m in msg_list:
                if hasattr(m, "content"):
                    input_data.append({"role": getattr(m, "type", "unknown"), "content": str(m.content)[:500]})
                else:
                    input_data.append(str(m)[:500])
        self._create_generation(run_id, model_name, input_data)

    def on_llm_end(
        self,
        response,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs,
    ):
        """Called after every LLM call."""
        output = ""
        if response.generations and response.generations[0]:
            first_gen = response.generations[0][0]
            if hasattr(first_gen, "message") and first_gen.message:
                output = getattr(first_gen.message, "content", "") or str(first_gen.message)
            elif hasattr(first_gen, "text"):
                output = first_gen.text or ""

        tok = (response.llm_output or {}).get("token_usage", {})
        self._end_generation(run_id, output, tok if isinstance(tok, dict) else None)

    def on_llm_error(
        self,
        error,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs,
    ):
        """Called when an LLM call fails."""
        self._end_generation(run_id, "", error=str(error))

    # ---- Legacy on_llm_start (for non-chat models / older langchain) ----

    def on_llm_start(
        self,
        serialized: dict,
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
        **kwargs,
    ):
        """Called before non-chat LLM calls (legacy support)."""
        llm_kwargs = serialized.get("kwargs", {})
        model_name = llm_kwargs.get("model", llm_kwargs.get("model_name", "unknown"))
        self._create_generation(run_id, model_name, prompts)


# === Module-level singleton tracer ===

_tracer: LangFuseTracer | None = None


def get_langfuse_callback():
    """Get a LangFuseTracer callback handler, or None if LangFuse is disabled.

    Returns the same singleton instance so LangChain always uses the
    same handler across all LLM calls.
    """
    from server.observability.langfuse_client import is_langfuse_enabled

    if not is_langfuse_enabled():
        return None

    global _tracer
    if _tracer is None:
        _tracer = LangFuseTracer()
    return _tracer
