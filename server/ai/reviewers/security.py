"""Security reviewer — detects SQL injection, XSS, hardcoded secrets, etc."""
from server.ai.reviewers.base import BaseReviewer
from server.ai.prompts.security_review import SECURITY_REVIEW_PROMPT


class SecurityReviewer(BaseReviewer):
    """Security code review agent — uses the strongest model for high accuracy."""

    name = "security"
    display_name = "Security Reviewer"
    severity_weight = 1.5  # Security issues are weighted higher

    def get_system_prompt(self) -> str:
        return SECURITY_REVIEW_PROMPT
