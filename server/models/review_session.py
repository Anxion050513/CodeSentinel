"""ReviewSession model — one row per PR review."""
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.database import Base
from server.models.base import BaseModel


class ReviewSession(BaseModel):
    """A single code review session for one PR."""

    __tablename__ = "review_sessions"

    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id"), nullable=False
    )
    pr_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pr_title: Mapped[str] = mapped_column(String(512), nullable=False)
    branch_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending"
    )  # pending / reviewing / completed / failed
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stats: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    repository = relationship("Repository", lazy="selectin")
    findings = relationship(
        "ReviewFinding", back_populates="session", lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def total_findings(self) -> int:
        """Return cached findings count, avoiding lazy-load after session close."""
        # Use cached value if set (set by review_service after saving findings)
        if hasattr(self, "_cached_findings_count"):
            return self._cached_findings_count
        # Fallback: try lazy load (only works if session is still open)
        try:
            return len(self.findings) if self.findings else 0
        except Exception:
            return 0

    @property
    def severity_counts(self) -> dict:
        """Return cached severity counts, avoiding lazy-load after session close."""
        if hasattr(self, "_cached_severity_counts"):
            return self._cached_severity_counts
        # Fallback
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        try:
            if self.findings:
                for f in self.findings:
                    sev = f.severity
                    if sev in counts:
                        counts[sev] += 1
        except Exception:
            pass
        return counts

    @property
    def formatted_stats(self) -> str:
        if not self.stats:
            return "N/A"
        return (
            f"{self.stats.get('total_files', 0)} files, "
            f"+{self.stats.get('total_additions', 0)} "
            f"-{self.stats.get('total_deletions', 0)}"
        )

    def __repr__(self) -> str:
        return (
            f"<ReviewSession {self.id[:8]}... "
            f"PR#{self.pr_number} status={self.status}>"
        )