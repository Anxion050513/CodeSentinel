"""Review API schemas."""
from datetime import datetime
from pydantic import BaseModel, Field


class ReviewTriggerRequest(BaseModel):
    repo_id: str
    pr_number: int
    incremental: bool = True  # If True, try incremental review first (fall back to full if no previous session)
    force: bool = False  # If True, skip all cache/incremental — always run a fresh full review


class ReviewStatusResponse(BaseModel):
    session_id: str
    pr_number: int
    pr_title: str
    status: str  # pending / reviewing / completed / failed
    total_findings: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None


class FindingItem(BaseModel):
    id: str
    reviewer_name: str
    severity: str
    file_path: str
    line_start: int
    line_end: int | None = None
    title: str
    description: str
    suggestion: str | None = None
    category: str | None = None
    is_verified: bool = False
    verification_result: str | None = None
    github_comment_id: int | None = None

    class Config:
        from_attributes = True


class ReviewReportResponse(BaseModel):
    session_id: str
    pr_number: int
    pr_title: str
    status: str
    summary: str | None = None
    stats: dict | None = None
    findings: list[FindingItem] = Field(default_factory=list)
    severity_counts: dict = Field(default_factory=dict)
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PublishResponse(BaseModel):
    session_id: str
    comments_posted: int
    status: str


class FindingFilter(BaseModel):
    reviewer_name: str | None = None
    severity: str | None = None
    category: str | None = None
    is_verified: bool | None = None
