"""Metrics plugin — tracks review statistics in-memory."""
from server.harness.hooks import ReviewHooks


class MetricsPlugin:
    """Collects runtime metrics for code reviews."""

    def __init__(self):
        self._metrics: dict[str, dict] = {}
        self._global: dict = {
            "total_reviews": 0,
            "total_findings": 0,
            "total_critical": 0,
            "total_high": 0,
        }

    def _ensure_session(self, session_id: str):
        if session_id not in self._metrics:
            self._metrics[session_id] = {
                "chunks_reviewed": 0,
                "findings": 0,
                "severity_counts": {},
                "errors": 0,
                "completed": False,
            }

    async def on_review_started(self, session_id, **kwargs):
        self._ensure_session(session_id)
        self._global["total_reviews"] += 1

    async def on_chunk_reviewed(self, session_id, **kwargs):
        self._ensure_session(session_id)
        self._metrics[session_id]["chunks_reviewed"] += 1

    async def on_finding_detected(self, session_id, severity, **kwargs):
        self._ensure_session(session_id)
        m = self._metrics[session_id]
        m["findings"] += 1
        m["severity_counts"][severity] = m["severity_counts"].get(severity, 0) + 1
        self._global["total_findings"] += 1
        if severity == "critical":
            self._global["total_critical"] += 1
        elif severity == "high":
            self._global["total_high"] += 1

    async def on_review_completed(self, session_id, **kwargs):
        self._ensure_session(session_id)
        self._metrics[session_id]["completed"] = True

    async def on_review_failed(self, session_id, **kwargs):
        self._ensure_session(session_id)
        self._metrics[session_id]["errors"] += 1

    def get_session_stats(self, session_id: str) -> dict:
        """Get current stats for a session."""
        return self._metrics.get(session_id, {
            "chunks_reviewed": 0,
            "findings": 0,
            "completed": False,
        })

    def get_global_stats(self) -> dict:
        """Get global review statistics."""
        return dict(self._global)
