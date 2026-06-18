"""Event data classes for the code review lifecycle."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ReviewEvent:
    """Base event for code review lifecycle."""
    session_id: str
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ReviewStarted(ReviewEvent):
    """Fired when a review session begins."""
    repository: str = ""
    pr_number: int = 0


@dataclass
class ChunkReviewCompleted(ReviewEvent):
    """Fired after a reviewer finishes analyzing one diff chunk."""
    reviewer_name: str = ""
    file_path: str = ""
    findings_count: int = 0


@dataclass
class FindingDetected(ReviewEvent):
    """Fired when a reviewer detects an issue."""
    reviewer_name: str = ""
    severity: str = "medium"
    category: str = ""
    file_path: str = ""
    line_start: int = 0


@dataclass
class ReviewAggregated(ReviewEvent):
    """Fired after findings are aggregated and deduplicated."""
    raw_findings: int = 0
    merged_findings: int = 0


@dataclass
class ReviewCompleted(ReviewEvent):
    """Fired when a review session completes successfully."""
    findings_count: int = 0
    severity_counts: dict = field(default_factory=dict)


@dataclass
class ReviewFailed(ReviewEvent):
    """Fired when a review session fails with an error."""
    error: str = ""


@dataclass
class ReviewPublished(ReviewEvent):
    """Fired when findings are published as GitHub PR comments."""
    comments_posted: int = 0
