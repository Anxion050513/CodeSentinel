"""Style reviewer — detects naming issues, code duplication, maintainability problems."""
from server.ai.reviewers.base import BaseReviewer
from server.ai.prompts.style_review import STYLE_REVIEW_PROMPT


class StyleReviewer(BaseReviewer):
    """Style/maintainability code review agent — uses a smaller, cheaper model."""

    name = "style"
    display_name = "Style Reviewer"
    severity_weight = 0.5  # Style issues are weighted lower

    def get_system_prompt(self) -> str:
        return STYLE_REVIEW_PROMPT
