"""GitHub API service — fetch PRs, diffs, post comments."""
import base64
import logging
from datetime import datetime

import httpx

from server.config import settings

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubService:
    """Encapsulates GitHub REST API calls.

    Uses GitHub App installation tokens or a personal access token for
    authentication. Token is stored encrypted in the database and
    decrypted at runtime.
    """

    # Encoding fallback chain for decoding file/diff content.
    # Repos with Chinese comments/strings may use GBK instead of UTF-8.
    _FALLBACK_ENCODINGS = ("gb18030", "gbk", "gb2312", "latin-1")

    def __init__(self, token: str):
        self.token = token
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-code-review-bot/0.1",
        }

    @staticmethod
    def _decode_bytes(data: bytes, source_hint: str = "") -> str:
        """Decode bytes to str with encoding fallback.

        Tries UTF-8 first (the overwhelming default for git repos),
        then falls back through common Chinese encodings for repos
        with legacy GBK/GB2312 files. latin-1 is the last resort
        (never fails — it decodes every byte 1:1).

        Args:
            data: Raw bytes to decode.
            source_hint: Optional label for log messages (e.g. "PR diff", "file src/x.php").
        """
        if isinstance(data, str):
            return data
        # Fast path: UTF-8
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        # Fallback chain
        for enc in GitHubService._FALLBACK_ENCODINGS:
            try:
                decoded = data.decode(enc)
                logger.info(
                    "Decoded %s as %s (UTF-8 failed at byte position with 0x%02x)",
                    source_hint or "content", enc, data[0] if data else 0,
                )
                return decoded
            except (UnicodeDecodeError, UnicodeError):
                continue
        # Unreachable (latin-1 never fails), but safety net
        return data.decode("utf-8", errors="replace")

    async def _request(
        self, method: str, path: str, **kwargs
    ) -> dict | list | bytes:
        """Make an authenticated request to GitHub API."""
        url = f"{GITHUB_API}{path}"
        # Merge caller-specified headers with default auth headers
        extra_headers = kwargs.pop("headers", {})
        merged_headers = {**self._headers, **extra_headers}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method, url, headers=merged_headers, **kwargs
            )
            response.raise_for_status()

            if "application/json" in response.headers.get("content-type", ""):
                return response.json()
            return response.content

    async def get_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict:
        """Fetch PR metadata."""
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pr_number}"
        )

    async def get_pr_diff(
        self, owner: str, repo: str, pr_number: int
    ) -> str:
        """Fetch the raw diff for a PR."""
        content = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={**self._headers, "Accept": "application/vnd.github.v3.diff"},
        )
        if isinstance(content, str):
            return content
        return self._decode_bytes(content, f"PR #{pr_number} diff")

    async def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Fetch the list of changed files in a PR."""
        return await self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pr_number}/files"
        )

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str
    ) -> str:
        """Fetch file content at a specific ref."""
        result = await self._request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        )
        if isinstance(result, dict) and result.get("content"):
            raw = base64.b64decode(result["content"])
            return self._decode_bytes(raw, f"file {path}")
        return ""

    async def post_pr_comment(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
        body: str,
    ) -> dict:
        """Post a simple comment on a PR (issue comment).

        Simpler and more reliable than create_review — doesn't require
        commit_sha or inline comments. PRs are also GitHub Issues.
        """
        return await self._request(
            "POST",
            f"/repos/{owner}/{repo_name}/issues/{pr_number}/comments",
            json={"body": body},
        )

    async def post_review_comment(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
        commit_sha: str,
        body: str,
        path: str,
        line: int,
        side: str = "RIGHT",
    ) -> int:
        """Post an inline review comment on a PR.

        Returns the GitHub comment ID.
        """
        result = await self._request(
            "POST",
            f"/repos/{owner}/{repo_name}/pulls/{pr_number}/comments",
            json={
                "body": body,
                "commit_id": commit_sha,
                "path": path,
                "line": line,
                "side": side,
            },
        )
        return result["id"]

    async def create_review(
        self,
        owner: str,
        repo_name: str,
        pr_number: int,
        commit_sha: str,
        body: str,
        event: str = "COMMENT",
        comments: list[dict] | None = None,
    ) -> dict:
        """Create a full PR review with optional inline comments.

        Args:
            event: APPROVE, REQUEST_CHANGES, or COMMENT
            comments: list of {path, line, body} dicts
        """
        data = {
            "commit_id": commit_sha,
            "body": body,
            "event": event,
        }
        if comments:
            data["comments"] = comments

        return await self._request(
            "POST",
            f"/repos/{owner}/{repo_name}/pulls/{pr_number}/reviews",
            json=data,
        )

    async def compare_commits(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> dict | None:
        """Get incremental diff between two commits.

        Uses GitHub's compare API to get the files and diff only for changes
        between the two specified commits — ideal for incremental review.

        Returns:
            {"diff": str, "files": list[dict]} — the same shape as get_pr_diff + get_pr_files,
            or None if the commits are identical / comparison is not meaningful.
        """
        try:
            # Fetch changed files between the two commits
            files = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
            )
        except Exception as e:
            logger.warning("Failed to compare %s...%s: %s", base_sha[:8], head_sha[:8], e)
            return None

        if not isinstance(files, dict):
            return None

        # If no changes, return empty diff
        changed_files = files.get("files", [])
        if not changed_files:
            logger.info("No changes between %s and %s", base_sha[:8], head_sha[:8])
            return {"diff": "", "files": []}

        # Get the raw diff via the same endpoint with diff Accept header
        try:
            diff_text = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}",
                headers={"Accept": "application/vnd.github.v3.diff"},
            )
            if isinstance(diff_text, bytes):
                diff_text = self._decode_bytes(diff_text, f"compare {base_sha[:8]}...{head_sha[:8]}")
        except Exception as e:
            logger.warning("Failed to fetch compare diff, building from patches: %s", e)
            # Fallback: build diff from individual file patches
            parts = []
            for f in changed_files:
                patch = f.get("patch", "")
                if not patch:
                    continue
                filename = f.get("filename", "")
                parts.append(
                    f"diff --git a/{filename} b/{filename}\n"
                    f"--- a/{filename}\n"
                    f"+++ b/{filename}\n"
                    f"{patch}"
                )
            diff_text = "\n".join(parts)

        # Convert file dicts to match get_pr_files shape
        file_list = [
            {
                "filename": f.get("filename", ""),
                "status": f.get("status", "modified"),
                "additions": f.get("additions", 0),
                "deletions": f.get("deletions", 0),
                "changes": f.get("changes", 0),
            }
            for f in changed_files
        ]

        logger.info(
            "Compare %s...%s: %d files, +%d/-%d",
            base_sha[:8], head_sha[:8],
            len(file_list),
            sum(f["additions"] for f in file_list),
            sum(f["deletions"] for f in file_list),
        )
        return {"diff": diff_text, "files": file_list}

    async def get_recent_commits(
        self, owner: str, repo: str, filepath: str, limit: int = 5
    ) -> list[dict]:
        """Get recent commits for a file (for git blame context)."""
        return await self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits",
            params={"path": filepath, "per_page": limit},
        )
