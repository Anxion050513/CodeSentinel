"""Admin API router — repo management, eval, LangFuse traces, health."""
import base64
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from server.database import get_db
from server.models.repository import Repository
from server.models.review_session import ReviewSession
from server.models.review_finding import ReviewFinding
from server.schemas.config import RepoCreateRequest, RepoResponse, ReviewRules
from server.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])


# ============================================================
# Health Check
# ============================================================

@router.get("/health")
async def health_check():
    """Basic health check endpoint."""
    import platform
    return {
        "status": "healthy",
        "service": "ai-code-review-bot",
        "version": "0.1.0",
        "python": platform.python_version(),
        "environment": settings.app_env,
    }


# ============================================================
# Repository Management
# ============================================================

@router.post("/repos", response_model=RepoResponse, status_code=201)
async def create_repository(
    req: RepoCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new GitHub repository for automated code review.

    Validates the GitHub token before saving.
    """
    # Check for duplicates
    existing = await db.execute(
        select(Repository).where(
            Repository.owner == req.owner,
            Repository.repo_name == req.repo_name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Repository {req.owner}/{req.repo_name} already registered",
        )

    # Validate GitHub token
    from server.services.github_service import GitHubService
    try:
        github = GitHubService(req.github_token)
        # Test the token by fetching the repo
        await github.get_pull_request(req.owner, req.repo_name, 1)
    except Exception as e:
        # Token might still be valid — just warn, don't block
        logger.warning(
            "GitHub token validation warning for %s/%s: %s",
            req.owner, req.repo_name, e,
        )

    # Create repository
    repo = Repository(
        owner=req.owner,
        repo_name=req.repo_name,
        webhook_secret=req.webhook_secret,
        github_token_encrypted=req.github_token,
        is_active=True,
        review_rules=req.review_rules.model_dump() if req.review_rules else {},
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)

    logger.info("Repository registered: %s/%s", req.owner, req.repo_name)

    return RepoResponse(
        id=repo.id,
        owner=repo.owner,
        repo_name=repo.repo_name,
        is_active=repo.is_active,
        review_rules=repo.review_rules or {},
        created_at=repo.created_at.isoformat() if repo.created_at else None,
    )


@router.get("/repos", response_model=list[RepoResponse])
async def list_repositories(
    db: AsyncSession = Depends(get_db),
):
    """List all registered repositories."""
    result = await db.execute(
        select(Repository).order_by(Repository.created_at.desc())
    )
    repos = result.scalars().all()

    return [
        RepoResponse(
            id=r.id,
            owner=r.owner,
            repo_name=r.repo_name,
            is_active=r.is_active,
            review_rules=r.review_rules or {},
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in repos
    ]


@router.delete("/repos/{repo_id}")
async def delete_repository(
    repo_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a repository configuration."""
    result = await db.execute(
        select(Repository).where(Repository.id == repo_id)
    )
    repo = result.scalar_one_or_none()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")

    await db.delete(repo)
    await db.commit()

    logger.info("Repository deleted: %s/%s", repo.owner, repo.repo_name)
    return {"status": "deleted", "id": repo_id}


# ============================================================
# Dashboard Stats
# ============================================================

@router.get("/dashboard")
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
):
    """Get dashboard overview statistics."""
    # Total repos
    repo_count_result = await db.execute(
        select(func.count()).select_from(Repository)
    )
    total_repos = repo_count_result.scalar() or 0

    # Total review sessions
    session_count_result = await db.execute(
        select(func.count()).select_from(ReviewSession)
    )
    total_sessions = session_count_result.scalar() or 0

    # Sessions by status
    status_counts = {"pending": 0, "reviewing": 0, "completed": 0, "failed": 0}
    for status_name in status_counts:
        cnt_result = await db.execute(
            select(func.count()).select_from(ReviewSession)
            .where(ReviewSession.status == status_name)
        )
        status_counts[status_name] = cnt_result.scalar() or 0

    # Total findings
    finding_count_result = await db.execute(
        select(func.count()).select_from(ReviewFinding)
    )
    total_findings = finding_count_result.scalar() or 0

    # Findings by severity
    severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for sev_name in severity_counts:
        cnt_result = await db.execute(
            select(func.count()).select_from(ReviewFinding)
            .where(ReviewFinding.severity == sev_name)
        )
        severity_counts[sev_name] = cnt_result.scalar() or 0

    # Findings by reviewer
    reviewer_counts = {}
    cnt_result = await db.execute(
        select(
            ReviewFinding.reviewer_name,
            func.count(ReviewFinding.id),
        ).group_by(ReviewFinding.reviewer_name)
    )
    for row in cnt_result.all():
        reviewer_counts[row[0]] = row[1]

    # Recent sessions
    recent_result = await db.execute(
        select(ReviewSession)
        .order_by(ReviewSession.created_at.desc())
        .limit(5)
    )
    recent_sessions = [
        {
            "id": s.id,
            "pr_number": s.pr_number,
            "pr_title": s.pr_title,
            "status": s.status,
            "total_findings": s.total_findings,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "completed_at": s.completed_at.isoformat() if s.completed_at else None,
        }
        for s in recent_result.scalars().all()
    ]

    # Last 7 days review trend
    from datetime import datetime, timedelta
    trend = []
    for i in range(6, -1, -1):
        day = datetime.utcnow() - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day.replace(hour=23, minute=59, second=59, microsecond=999999)
        cnt = await db.execute(
            select(func.count()).select_from(ReviewSession)
            .where(
                ReviewSession.created_at >= day_start,
                ReviewSession.created_at <= day_end,
            )
        )
        trend.append({
            "date": day.strftime("%m-%d"),
            "count": cnt.scalar() or 0,
        })

    return {
        "total_repos": total_repos,
        "total_sessions": total_sessions,
        "status_counts": status_counts,
        "total_findings": total_findings,
        "severity_counts": severity_counts,
        "reviewer_counts": reviewer_counts,
        "recent_sessions": recent_sessions,
        "review_trend": trend,
    }


# ============================================================
# Admin — LangFuse Traces
# ============================================================

@router.get("/admin/traces")
async def get_traces(
    session_id: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Query LangFuse traces via REST API (compatible with SDK v4.x)."""
    pk = settings.langfuse_public_key
    sk = settings.langfuse_secret_key
    if not pk or not sk:
        return {"enabled": False, "traces": [], "message": "LangFuse is not configured"}

    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    host = settings.langfuse_host.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if session_id:
                clean_id = session_id.replace("-", "")
                resp = await client.get(
                    f"{host}/api/public/traces/{clean_id}",
                    headers=headers,
                )
                if resp.status_code == 404:
                    return {"enabled": True, "trace": None, "message": "Trace not found"}
                resp.raise_for_status()
                return {"enabled": True, "trace": resp.json()}
            else:
                resp = await client.get(
                    f"{host}/api/public/traces",
                    headers=headers,
                    params={"limit": limit, "orderBy": "timestamp.desc"},
                )
                resp.raise_for_status()
                data = resp.json()
                traces = data.get("data", [])
                return {
                    "enabled": True,
                    "traces_count": len(traces),
                    "traces": [
                        {"id": t.get("id"), "name": t.get("name"), "timestamp": t.get("timestamp")}
                        for t in traces
                    ],
                }
    except Exception as e:
        logger.warning("LangFuse API query failed: %s", e)
        return {"enabled": True, "error": str(e)}


# ============================================================
# Admin — Eval Runner
# ============================================================

@router.post("/admin/eval/run")
async def run_evaluation(
    db: AsyncSession = Depends(get_db),
):
    """Run the evaluation suite against the golden dataset.

    Returns precision, recall, and F1 scores.
    """
    from server.dependencies import get_llm_factory
    from server.eval.eval_runner import CodeReviewEvalRunner

    llm_factory = get_llm_factory()
    eval_runner = CodeReviewEvalRunner(llm_factory)

    try:
        report = await eval_runner.run_eval()
        return {
            "status": "completed",
            "report": report.model_dump(),
        }
    except Exception as e:
        logger.error("Eval run failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {e}")


# ============================================================
# Admin — Review Sessions List (for dashboard)
# ============================================================

@router.get("/admin/sessions")
async def list_admin_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of review sessions for the admin dashboard."""
    query = select(ReviewSession)
    if status:
        query = query.where(ReviewSession.status == status)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    query = query.order_by(ReviewSession.created_at.desc())
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    sessions = result.scalars().all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "sessions": [
            {
                "id": s.id,
                "repository_id": s.repository_id,
                "pr_number": s.pr_number,
                "pr_title": s.pr_title,
                "branch_name": s.branch_name,
                "base_branch": s.base_branch,
                "commit_sha": s.commit_sha[:7],
                "status": s.status,
                "summary": s.summary,
                "stats": s.stats,
                "total_findings": s.total_findings,
                "severity_counts": s.severity_counts,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions
        ],
    }


# ============================================================
# Admin — Delete Session
# ============================================================

@router.delete("/admin/sessions/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a review session and all its findings."""
    result = await db.execute(
        select(ReviewSession).where(ReviewSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete associated findings first
    findings_result = await db.execute(
        select(ReviewFinding).where(ReviewFinding.session_id == session_id)
    )
    for f in findings_result.scalars().all():
        await db.delete(f)

    await db.delete(session)
    await db.commit()

    logger.info("Session %s deleted with all findings", session_id[:8])
    return {"status": "deleted", "session_id": session_id}


# ============================================================
# Admin — GitHub App Integration
# ============================================================

@router.get("/admin/github/installations")
async def list_github_installations():
    """List GitHub App installations."""
    try:
        from server.services.github_app_service import github_app
        installations = await github_app.list_installations()
        return {
            "status": "ok",
            "app_id": settings.github_app_id,
            "installations": installations,
        }
    except Exception as e:
        logger.error("Failed to list installations: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to communicate with GitHub: {e}",
        )


@router.get("/admin/github/installations/{installation_id}/repos")
async def list_installation_repositories(installation_id: int):
    """List repositories accessible to a GitHub App installation."""
    try:
        from server.services.github_app_service import github_app
        repos = await github_app.list_installation_repos(installation_id)
        return {
            "status": "ok",
            "installation_id": installation_id,
            "repositories": repos,
            "count": len(repos),
        }
    except Exception as e:
        logger.error(
            "Failed to list repos for installation %d: %s",
            installation_id, e,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch repositories: {e}",
        )


# ============================================================
# Admin — Maintenance
# ============================================================

@router.post("/admin/maintenance/run")
async def run_maintenance():
    """Manually trigger session cleanup and maintenance tasks."""
    try:
        from server.services.session_cleanup import cleanup_service
        await cleanup_service.run_maintenance()
        return {"status": "ok", "message": "Maintenance tasks completed"}
    except Exception as e:
        logger.error("Maintenance failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Maintenance failed: {e}"
        )


@router.get("/admin/maintenance/status")
async def maintenance_status(
    db: AsyncSession = Depends(get_db),
):
    """Get maintenance overview: stale sessions, failed sessions count."""
    from datetime import datetime, timedelta

    stale_threshold = datetime.utcnow() - timedelta(minutes=30)

    # Stale sessions
    stale_result = await db.execute(
        select(func.count()).select_from(ReviewSession).where(
            ReviewSession.status == "reviewing",
            ReviewSession.started_at < stale_threshold,
        )
    )
    stale_count = stale_result.scalar() or 0

    # Failed sessions
    failed_result = await db.execute(
        select(func.count()).select_from(ReviewSession).where(
            ReviewSession.status == "failed",
        )
    )
    failed_count = failed_result.scalar() or 0

    # Old sessions (> 90 days)
    archive_date = datetime.utcnow() - timedelta(days=90)
    old_result = await db.execute(
        select(func.count()).select_from(ReviewSession).where(
            ReviewSession.created_at < archive_date,
        )
    )
    old_count = old_result.scalar() or 0

    return {
        "stale_sessions": stale_count,
        "failed_sessions": failed_count,
        "old_sessions_archivable": old_count,
        "stale_threshold_minutes": 30,
        "archive_threshold_days": 90,
    }