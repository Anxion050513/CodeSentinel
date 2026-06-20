"""Repository configuration schemas."""
from pydantic import BaseModel, Field


class ReviewRules(BaseModel):
    """Which reviewers to enable and their configuration."""
    security: bool = True
    performance: bool = True
    logic: bool = True
    style: bool = True
    min_severity: str = "low"  # critical / high / medium / low / info
    auto_publish: bool = False
    max_findings_per_reviewer: int = 20
    ignore_patterns: list[str] = Field(default_factory=list)


class RepoCreateRequest(BaseModel):
    owner: str
    repo_name: str
    webhook_secret: str
    github_token: str
    review_rules: ReviewRules = Field(default_factory=ReviewRules)


class RepoResponse(BaseModel):
    id: str
    owner: str
    repo_name: str
    is_active: bool
    review_rules: dict
    created_at: str | None = None

    class Config:
        from_attributes = True
