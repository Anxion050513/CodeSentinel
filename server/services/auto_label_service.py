"""Auto-label service — adds labels to PRs based on review findings.

Automatically applies labels like:
- bug — when logic/security issues found
- security — when security issues found
- performance — when performance issues found
- needs-review — when style issues found
- ai-reviewed — always applied after review
"""
import logging

from server.models.review_session import ReviewSession
from server.models.review_finding import ReviewFinding
from server.services.github_service import GitHubService

logger = logging.getLogger(__name__)

# Label mapping: finding categories → PR labels
CATEGORY_LABELS = {
    "sql_injection": ["security", "bug"],
    "xss": ["security", "bug"],
    "hardcoded_secret": ["security"],
    "insecure_auth": ["security", "bug"],
    "path_traversal": ["security", "bug"],
    "command_injection": ["security", "bug"],
    "insecure_crypto": ["security"],
    "ssrf": ["security", "bug"],
    "input_validation": ["security"],
    "n_plus_1": ["performance"],
    "memory_leak": ["performance", "bug"],
    "inefficient_algorithm": ["performance"],
    "missing_cache": ["performance"],
    "blocking_io": ["performance"],
    "large_payload": ["performance"],
    "null_pointer": ["bug"],
    "boundary": ["bug"],
    "exception_handling": ["bug"],
    "race_condition": ["bug"],
    "boolean_logic": ["bug"],
    "infinite_loop": ["bug"],
    "naming": ["style"],
    "duplication": ["style"],
    "complexity": ["style"],
}

# Severity → label
SEVERITY_LABELS = {
    "critical": "critical",
    "high": "high-priority",
}


class AutoLabelService:
    """Automatically labels PRs based on AI review findings."""

    # Standard label descriptions
    LABEL_DESCRIPTIONS = {
        "ai-reviewed": "Reviewed by AI Code Review Bot",
        "bug": "Potential bug found by AI review",
        "security": "Security issue found by AI review",
        "performance": "Performance issue found by AI review",
        "style": "Style/maintainability suggestion from AI review",
        "critical": "Critical issue requires immediate attention",
        "high-priority": "High priority issue found",
    }

    # Label colors (GitHub hex without #)
    LABEL_COLORS = {
        "ai-reviewed": "1D76DB",
        "bug": "D73A4A",
        "security": "B60205",
        "performance": "FBCA04",
        "style": "BFDADC",
        "critical": "B60205",
        "high-priority": "FF8C00",
    }

    async def apply_labels(
        self,
        github: GitHubService,
        owner: str,
        repo: str,
        pr_number: int,
        session: ReviewSession,
        findings: list[ReviewFinding],
    ):
        """Apply labels to a PR based on review findings.

        Args:
            github: GitHubService instance with valid token
            owner: Repository owner
            repo: Repository name
            pr_number: PR number
            session: The review session
            findings: List of ReviewFinding ORM objects
        """
        labels_to_add: set[str] = {"ai-reviewed"}

        # Analyze findings to determine labels
        for finding in findings:
            # Category-based labels
            category = finding.category or ""
            for cat_key, labels in CATEGORY_LABELS.items():
                if cat_key in category:
                    labels_to_add.update(labels)

            # Severity-based labels
            severity_label = SEVERITY_LABELS.get(finding.severity)
            if severity_label:
                labels_to_add.add(severity_label)

        # Also determine reviewer-based labels
        reviewer_categories = set(f.reviewer_name for f in findings)
        for reviewer in reviewer_categories:
            if reviewer == "security":
                labels_to_add.add("security")
            elif reviewer == "performance":
                labels_to_add.add("performance")
            elif reviewer == "style":
                labels_to_add.add("style")

        if not findings:
            # No issues found — just mark as reviewed
            labels_to_add = {"ai-reviewed"}

        logger.info(
            "Auto-labeling PR #%d with: %s",
            pr_number, ", ".join(sorted(labels_to_add)),
        )

        try:
            await self._ensure_labels_exist(
                github, owner, repo, labels_to_add
            )
            await self._add_labels_to_pr(
                github, owner, repo, pr_number, list(labels_to_add)
            )
            logger.info(
                "Successfully labeled PR #%d with %d label(s)",
                pr_number, len(labels_to_add),
            )
        except Exception as e:
            logger.error("Failed to auto-label PR #%d: %s", pr_number, e)

    async def _ensure_labels_exist(
        self,
        github: GitHubService,
        owner: str,
        repo: str,
        labels: set[str],
    ):
        """Ensure all required labels exist in the repository. Create any missing ones."""
        # Get existing labels
        existing_labels = await github._request(
            "GET", f"/repos/{owner}/{repo}/labels"
        )
        existing_names = {
            lbl["name"] for lbl in existing_labels
            if isinstance(existing_labels, list)
        }

        # Create missing labels
        for label_name in labels:
            if label_name in existing_names:
                continue

            color = self.LABEL_COLORS.get(label_name, "BFDADC")
            description = self.LABEL_DESCRIPTIONS.get(label_name, "")

            try:
                await github._request(
                    "POST",
                    f"/repos/{owner}/{repo}/labels",
                    json={
                        "name": label_name,
                        "color": color,
                        "description": description,
                    },
                )
                logger.debug("Created label: %s (#%s)", label_name, color)
            except Exception as e:
                logger.warning("Failed to create label '%s': %s", label_name, e)

    async def _add_labels_to_pr(
        self,
        github: GitHubService,
        owner: str,
        repo: str,
        pr_number: int,
        labels: list[str],
    ):
        """Apply labels to a PR."""
        await github._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{pr_number}/labels",
            json={"labels": labels},
        )


# Singleton
auto_labeler = AutoLabelService()