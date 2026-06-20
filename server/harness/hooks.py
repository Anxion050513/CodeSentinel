"""pluggy hook specifications for the code review lifecycle."""
import pluggy

hookspec = pluggy.HookspecMarker("ai_code_reviewer")


class ReviewHooks:
    """Hook specification for code review events.

    Plugins implement these hooks to react to review lifecycle events.
    Same pattern as InterviewHooks from the interview system.
    """

    @hookspec
    async def on_review_started(
        self, session_id: str, repository: str, pr_number: int
    ):
        """Fired when a review session starts."""

    @hookspec
    async def on_chunk_reviewed(
        self,
        session_id: str,
        reviewer_name: str,
        file_path: str,
        findings_count: int,
    ):
        """Fired after a single reviewer finishes a chunk."""

    @hookspec
    async def on_finding_detected(
        self,
        session_id: str,
        reviewer_name: str,
        severity: str,
        category: str,
        file_path: str,
        line_start: int,
    ):
        """Fired when a reviewer detects an issue."""

    @hookspec
    async def on_review_aggregated(
        self, session_id: str, raw_findings: int, merged_findings: int
    ):
        """Fired after findings are aggregated and deduplicated."""

    @hookspec
    async def on_review_completed(
        self, session_id: str, findings_count: int, severity_counts: dict
    ):
        """Fired when a review completes successfully."""

    @hookspec
    async def on_review_failed(self, session_id: str, error: str):
        """Fired when a review fails with an error."""

    @hookspec
    async def on_review_published(self, session_id: str, comments_posted: int):
        """Fired when findings are published as GitHub PR comments."""

    @hookspec
    async def on_error(self, session_id: str, error: Exception, context: dict):
        """Fired on any error during review processing."""
