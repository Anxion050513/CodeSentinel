"""Logger plugin — logs all review events to structured log."""
import json
import logging

from server.harness.hooks import ReviewHooks

logger = logging.getLogger("ai_code_reviewer.events")


class LoggerPlugin:
    """Logs every review lifecycle event to a structured logger."""

    async def on_review_started(self, session_id, repository, pr_number):
        logger.info(
            f"REVIEW_STARTED | session={session_id} | "
            f"repo={repository} | pr=#{pr_number}"
        )

    async def on_chunk_reviewed(self, session_id, reviewer_name, file_path, findings_count):
        logger.debug(
            f"CHUNK_REVIEWED | session={session_id} | "
            f"reviewer={reviewer_name} | file={file_path} | "
            f"findings={findings_count}"
        )

    async def on_finding_detected(self, session_id, reviewer_name, severity, category, file_path, line_start):
        logger.info(
            f"FINDING_DETECTED | session={session_id} | "
            f"reviewer={reviewer_name} | severity={severity} | "
            f"category={category} | file={file_path}:{line_start}"
        )

    async def on_review_aggregated(self, session_id, raw_findings, merged_findings):
        logger.info(
            f"REVIEW_AGGREGATED | session={session_id} | "
            f"raw={raw_findings} | merged={merged_findings} | "
            f"dedup_rate={((raw_findings - merged_findings) / max(raw_findings, 1)) * 100:.1f}%"
        )

    async def on_review_completed(self, session_id, findings_count, severity_counts):
        logger.info(
            f"REVIEW_COMPLETED | session={session_id} | "
            f"findings={findings_count} | severities={severity_counts}"
        )

    async def on_review_failed(self, session_id, error):
        logger.error(
            f"REVIEW_FAILED | session={session_id} | error={error}"
        )

    async def on_review_published(self, session_id, comments_posted):
        logger.info(
            f"REVIEW_PUBLISHED | session={session_id} | "
            f"comments={comments_posted}"
        )

    async def on_error(self, session_id, error, context):
        logger.error(
            f"REVIEW_ERROR | session={session_id} | "
            f"error={error} | context={json.dumps(context, default=str)}"
        )
