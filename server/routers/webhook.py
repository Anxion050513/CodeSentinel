"""GitHub Webhook router — handles incoming PR events."""
import hashlib
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.database import get_db
from server.models.repository import Repository
from server.schemas.webhook import GitHubWebhookPayload
from server.services.github_service import GitHubService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhook", tags=["webhook"])


async def verify_webhook_signature(
    request: Request, secret: str
) -> bool:
    """Verify GitHub webhook HMAC-SHA256 signature.

    Returns True if the signature matches or verification is disabled.
    """
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not signature:
        logger.warning("No webhook signature header found")
        # In development, allow unsigned requests
        if settings.is_development:
            return True
        return False

    body = await request.body()
    expected = (
        "sha256="
        + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    )

    if not hmac.compare_digest(signature, expected):
        logger.warning("Webhook signature verification failed")
        if settings.is_development:
            return True  # Allow in dev
        return False

    return True


async def _process_webhook_event(
    payload: GitHubWebhookPayload,
    repo: Repository,
    db: AsyncSession,
):
    """Background task: process a webhook PR event."""
    from server.services.review_service import ReviewService

    pr = payload.pull_request
    action = payload.action

    # Only handle PR open/sync events
    if action not in ("opened", "synchronize", "reopened"):
        logger.info("Ignoring PR action: %s", action)
        return

    branch_name = pr.head.get("ref", "")
    base_branch = pr.base.get("ref", "")
    commit_sha = pr.head.get("sha", "")

    if not commit_sha:
        logger.warning("No commit SHA in PR head for PR #%d", pr.number)
        return

    logger.info(
        "Processing PR #%d (%s) for %s/%s",
        pr.number, action, repo.owner, repo.repo_name,
    )

    review_service = ReviewService()
    try:
        session = await review_service.start_review(
            db=db,
            repository=repo,
            pr_number=pr.number,
            pr_title=pr.title,
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            action=action,
        )
        logger.info(
            "Review session %s completed: %d findings",
            session.id, session.total_findings,
        )
    except Exception as e:
        logger.error("Review failed for PR #%d: %s", pr.number, e, exc_info=True)


@router.post("/github")
async def handle_github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: GitHubWebhookPayload,
    x_github_event: str = Header(default="ping"),
):
    """Receive GitHub webhook events for PRs.

    Validates the webhook signature, looks up the repository configuration,
    and triggers a review in the background.
    """
    # Look up the repository
    owner = payload.repository.owner.get("login", "") or payload.repository.owner.get("name", "")
    repo_name = payload.repository.name

    if not owner or not repo_name:
        logger.warning("Could not extract owner/repo from webhook payload")
        return {"status": "ignored", "reason": "missing owner/repo info"}

    # Use get_db as context manager
    from server.database import async_session as session_factory

    async with session_factory() as db:
        result = await db.execute(
            select(Repository).where(
                Repository.owner == owner,
                Repository.repo_name == repo_name,
                Repository.is_active == True,  # noqa: E712
            )
        )
        repo = result.scalar_one_or_none()

        if not repo:
            logger.info(
                "No registered repository for %s/%s", owner, repo_name
            )
            return {"status": "ignored", "reason": "repository not registered"}

        # Verify webhook signature
        valid = await verify_webhook_signature(request, repo.webhook_secret)
        if not valid:
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        # Handle ping event (GitHub sends this when webhook is first configured)
        if x_github_event == "ping":
            logger.info("Ping received for %s/%s", owner, repo_name)
            return {"status": "ok", "message": "Webhook configured successfully"}

        # Only process PR events
        if x_github_event != "pull_request":
            return {"status": "ignored", "reason": f"event type: {x_github_event}"}

        # Trigger review in background
        background_tasks.add_task(_process_webhook_event, payload, repo, db)

        return {
            "status": "accepted",
            "repository": f"{owner}/{repo_name}",
            "pr_number": payload.pull_request.number,
            "message": "Review queued",
        }