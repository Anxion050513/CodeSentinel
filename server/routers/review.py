"""Review API router — trigger, status, report, findings, publish."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from server.database import get_db
from server.models.repository import Repository
from server.models.review_session import ReviewSession
from server.models.review_finding import ReviewFinding
from server.schemas.review import (
    FindingFilter,
    FindingItem,
    PublishResponse,
    ReviewReportResponse,
    ReviewStatusResponse,
    ReviewTriggerRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/review", tags=["review"])


@router.post("/trigger", response_model=ReviewStatusResponse)
async def trigger_review(
    req: ReviewTriggerRequest,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a code review for a PR.

    Fetches PR details from GitHub, then runs the full review pipeline.
    """
    # Look up repository
    result = await db.execute(
        select(Repository).where(
            Repository.id == req.repo_id,
            Repository.is_active == True,  # noqa: E712
        )
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found or inactive")

    # Fetch PR details from GitHub
    from server.services.github_service import GitHubService
    github = GitHubService(repo.github_token_encrypted)

    try:
        pr = await github.get_pull_request(repo.owner, repo.repo_name, req.pr_number)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch PR from GitHub: {e}",
        )

    head = pr.get("head", {})
    base = pr.get("base", {})

    # Start review
    from server.services.review_service import ReviewService
    review_service = ReviewService()

    # Determine action: if incremental=True and there might be a previous session,
    # use "synchronize" to trigger the incremental path
    action = "synchronize" if req.incremental else "opened"

    try:
        session = await review_service.start_review(
            db=db,
            repository=repo,
            pr_number=req.pr_number,
            pr_title=pr.get("title", ""),
            branch_name=head.get("ref", ""),
            base_branch=base.get("ref", ""),
            commit_sha=head.get("sha", ""),
            action=action,
        )

        await db.commit()

        return ReviewStatusResponse(
            session_id=session.id,
            pr_number=session.pr_number,
            pr_title=session.pr_title,
            status=session.status,
            total_findings=session.total_findings,
            started_at=session.started_at,
            completed_at=session.completed_at,
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Review failed: {e}"
        )


@router.get("/{session_id}/status", response_model=ReviewStatusResponse)
async def get_review_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the current status of a review session."""
    result = await db.execute(
        select(ReviewSession).where(ReviewSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    return ReviewStatusResponse(
        session_id=session.id,
        pr_number=session.pr_number,
        pr_title=session.pr_title,
        status=session.status,
        total_findings=session.total_findings,
        started_at=session.started_at,
        completed_at=session.completed_at,
    )


@router.get("/{session_id}/report", response_model=ReviewReportResponse)
async def get_review_report(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the full review report with all findings."""
    result = await db.execute(
        select(ReviewSession).where(ReviewSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    # Load findings
    f_result = await db.execute(
        select(ReviewFinding)
        .where(ReviewFinding.session_id == session_id)
        .order_by(
            # Severity order: critical first
            func.field(
                ReviewFinding.severity,
                "critical", "high", "medium", "low", "info",
            ),
            ReviewFinding.file_path,
            ReviewFinding.line_start,
        )
    )
    findings = f_result.scalars().all()

    findings_items = [
        FindingItem(
            id=f.id,
            reviewer_name=f.reviewer_name,
            severity=f.severity,
            file_path=f.file_path,
            line_start=f.line_start,
            line_end=f.line_end,
            title=f.title,
            description=f.description,
            suggestion=f.suggestion,
            category=f.category,
            is_verified=f.is_verified,
            verification_result=f.verification_result,
            github_comment_id=f.github_comment_id,
        )
        for f in findings
    ]

    return ReviewReportResponse(
        session_id=session.id,
        pr_number=session.pr_number,
        pr_title=session.pr_title,
        status=session.status,
        summary=session.summary,
        stats=session.stats,
        findings=findings_items,
        severity_counts=session.severity_counts,
        started_at=session.started_at,
        completed_at=session.completed_at,
    )


@router.get("/{session_id}/findings", response_model=list[FindingItem])
async def get_review_findings(
    session_id: str,
    reviewer_name: str | None = Query(None),
    severity: str | None = Query(None),
    category: str | None = Query(None),
    is_verified: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Get findings for a review session with optional filters."""
    # Verify session exists
    session_result = await db.execute(
        select(ReviewSession.id).where(ReviewSession.id == session_id)
    )
    if not session_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Review session not found")

    # Build query with filters
    query = select(ReviewFinding).where(ReviewFinding.session_id == session_id)

    if reviewer_name:
        query = query.where(ReviewFinding.reviewer_name == reviewer_name)
    if severity:
        query = query.where(ReviewFinding.severity == severity)
    if category:
        query = query.where(ReviewFinding.category == category)
    if is_verified is not None:
        query = query.where(ReviewFinding.is_verified == is_verified)

    query = query.order_by(ReviewFinding.file_path, ReviewFinding.line_start)

    result = await db.execute(query)
    findings = result.scalars().all()

    return [
        FindingItem(
            id=f.id,
            reviewer_name=f.reviewer_name,
            severity=f.severity,
            file_path=f.file_path,
            line_start=f.line_start,
            line_end=f.line_end,
            title=f.title,
            description=f.description,
            suggestion=f.suggestion,
            category=f.category,
            is_verified=f.is_verified,
            verification_result=f.verification_result,
            github_comment_id=f.github_comment_id,
        )
        for f in findings
    ]


@router.post("/{session_id}/publish", response_model=PublishResponse)
async def publish_review(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Publish review findings as GitHub PR inline comments."""
    # Load session with repository
    result = await db.execute(
        select(ReviewSession).where(ReviewSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Review session not found")

    # Load repository
    repo_result = await db.execute(
        select(Repository).where(Repository.id == session.repository_id)
    )
    repo = repo_result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Load findings
    f_result = await db.execute(
        select(ReviewFinding).where(ReviewFinding.session_id == session_id)
    )
    findings = f_result.scalars().all()

    if not findings:
        raise HTTPException(status_code=400, detail="No findings to publish")

    # Publish to GitHub
    from server.services.github_service import GitHubService
    from server.services.review_service import ReviewService

    github = GitHubService(repo.github_token_encrypted)
    review_service = ReviewService()

    findings_dicts = [
        {
            "reviewer_name": f.reviewer_name,
            "severity": f.severity,
            "file_path": f.file_path,
            "line_start": f.line_start,
            "line_end": f.line_end,
            "title": f.title,
            "description": f.description,
            "suggestion": f.suggestion,
            "category": f.category,
            "is_verified": f.is_verified,
            "verification_result": f.verification_result,
        }
        for f in findings
    ]

    try:
        await review_service._publish_findings(
            github=github,
            repository=repo,
            session=session,
            findings=findings_dicts,
            db=db,
        )
        findings_count = len(findings)
        await db.commit()

        return PublishResponse(
            session_id=session_id,
            comments_posted=findings_count,  # all findings published in one summary review
            status="published",
        )
    except Exception as e:
        logger.error("Failed to publish findings: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to publish: {e}")


@router.get("/", response_model=list[ReviewStatusResponse])
async def list_reviews(
    repo_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List review sessions with optional filters.

    Supports pagination and filtering by repository and status.
    """
    query = select(ReviewSession)

    if repo_id:
        query = query.where(ReviewSession.repository_id == repo_id)
    if status:
        query = query.where(ReviewSession.status == status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply ordering and pagination
    query = query.order_by(ReviewSession.created_at.desc())
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    sessions = result.scalars().all()

    return [
        ReviewStatusResponse(
            session_id=s.id,
            pr_number=s.pr_number,
            pr_title=s.pr_title,
            status=s.status,
            total_findings=s.total_findings,
            started_at=s.started_at,
            completed_at=s.completed_at,
        )
        for s in sessions
    ]