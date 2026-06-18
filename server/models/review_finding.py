"""ReviewFinding model — individual issues found during review."""
from typing import Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.database import Base
from server.models.base import BaseModel


class ReviewFinding(BaseModel):
    """A single issue/finding discovered by one reviewer agent."""

    __tablename__ = "review_findings"

    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_sessions.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_name: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # security / performance / logic / style
    severity: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # critical / high / medium / low / info
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    line_start: Mapped[int] = mapped_column(Integer, nullable=False)
    line_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True
    )  # sql_injection / n_plus_1 / null_pointer / etc.
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    github_comment_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Relationships
    session = relationship("ReviewSession", back_populates="findings")

    @property
    def location(self) -> str:
        """Human-readable code location."""
        if self.line_end and self.line_end != self.line_start:
            return f"{self.file_path}:{self.line_start}-{self.line_end}"
        return f"{self.file_path}:{self.line_start}"

    @property
    def severity_emoji(self) -> str:
        emojis = {
            "critical": "🔴", "high": "🟠", "medium": "🟡",
            "low": "🟢", "info": "ℹ️",
        }
        return emojis.get(self.severity, "⚪")

    @property
    def is_high_severity(self) -> bool:
        return self.severity in ("critical", "high")

    def __repr__(self) -> str:
        return (
            f"<ReviewFinding [{self.severity}] {self.title} "
            f"@ {self.file_path}:{self.line_start}>"
        )