"""Performance reviewer — detects N+1 queries, memory leaks, inefficient algorithms."""
from server.ai.reviewers.base import BaseReviewer
from server.ai.prompts.performance_review import PERFORMANCE_REVIEW_PROMPT


class PerformanceReviewer(BaseReviewer):
    """Performance code review agent."""

    name = "performance"
    display_name = "Performance Reviewer"
    severity_weight = 1.0

    def get_system_prompt(self) -> str:
        return PERFORMANCE_REVIEW_PROMPT
