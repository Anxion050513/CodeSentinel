"""Logic reviewer — detects null pointers, boundary conditions, exception handling."""
from server.ai.reviewers.base import BaseReviewer
from server.ai.prompts.logic_review import LOGIC_REVIEW_PROMPT


class LogicReviewer(BaseReviewer):
    """Logic/correctness code review agent."""

    name = "logic"
    display_name = "Logic Reviewer"
    severity_weight = 1.0

    def get_system_prompt(self) -> str:
        return LOGIC_REVIEW_PROMPT
