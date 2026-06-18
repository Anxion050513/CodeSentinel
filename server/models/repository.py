"""Repository model — stores GitHub repo configuration and credentials."""
import json

from sqlalchemy import Boolean, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from server.models.base import BaseModel


class Repository(BaseModel):
    """GitHub repository configured for automated code review."""

    __tablename__ = "repositories"

    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    webhook_secret: Mapped[str] = mapped_column(String(255), nullable=False)
    github_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    review_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # id and created_at are inherited from BaseModel

    __table_args__ = (
        UniqueConstraint("owner", "repo_name", name="uk_owner_repo"),
    )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo_name}"

    @property
    def review_rules_dict(self) -> dict:
        """Ensure review_rules is always a dict."""
        if isinstance(self.review_rules, str):
            return json.loads(self.review_rules)
        return self.review_rules or {}

    def get_enabled_reviewers(self) -> list[str]:
        """Return list of enabled reviewer names."""
        rules = self.review_rules_dict
        reviewers = []
        if rules.get("security", True):
            reviewers.append("security")
        if rules.get("performance", True):
            reviewers.append("performance")
        if rules.get("logic", True):
            reviewers.append("logic")
        if rules.get("style", True):
            reviewers.append("style")
        return reviewers

    def __repr__(self) -> str:
        return f"<Repository {self.full_name} active={self.is_active}>"