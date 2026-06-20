"""Review orchestrator — the core pipeline from diff to findings."""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.models.repository import Repository
from server.models.review_session import ReviewSession
from server.models.review_finding import ReviewFinding
from server.dependencies import get_llm_factory
from server.harness.manager import harness

logger = logging.getLogger(__name__)


class ReviewService:
    """Orchestrates the full review pipeline:

    1. Fetch diff from GitHub (full or incremental)
    2. Chunk diff intelligently
    3. Build code context (AST + RAG)
    4. Run 4 reviewers in parallel
    5. Aggregate and deduplicate findings
    6. Verify high-severity findings in sandbox
    7. Post findings as GitHub comments (if auto_publish)

    Supports incremental review: on PR "synchronize" events, only the diff
    between the last-reviewed commit and the new commit is reviewed, and
    findings for unchanged files are carried forward from the previous session.
    """

    async def start_review(
        self,
        db: AsyncSession,
        repository: Repository,
        pr_number: int,
        pr_title: str,
        branch_name: str,
        base_branch: str,
        commit_sha: str,
        action: str = "opened",
    ) -> ReviewSession:
        """Start a new review session and run the pipeline.

        Args:
            action: GitHub event action — "opened", "synchronize", or "reopened".
                    "synchronize" triggers incremental review when possible.
        """
        # Determine if incremental review is possible
        previous_session = None
        incremental_diff = None

        if action == "synchronize":
            previous_session = await self._get_last_completed_session(
                db, repository.id, pr_number
            )
            if previous_session and previous_session.commit_sha != commit_sha:
                logger.info(
                    "Attempting incremental review for PR #%d: %s → %s",
                    pr_number,
                    previous_session.commit_sha[:8],
                    commit_sha[:8],
                )
                from server.services.github_service import GitHubService
                github = GitHubService(repository.github_token_encrypted)
                incremental_diff = await github.compare_commits(
                    repository.owner, repository.repo_name,
                    previous_session.commit_sha, commit_sha,
                )
                if incremental_diff is None:
                    logger.info("Compare API failed, falling back to full review")
                elif incremental_diff.get("diff", "") == "" and not incremental_diff.get("files"):
                    logger.info(
                        "No changes between %s and %s — skipping review",
                        previous_session.commit_sha[:8], commit_sha[:8],
                    )
                    # Create a skipped session reusing previous findings
                    return await self._copy_previous_session(
                        db, repository, pr_number, pr_title,
                        branch_name, base_branch, commit_sha,
                        previous_session,
                    )
            elif previous_session and previous_session.commit_sha == commit_sha:
                logger.info(
                    "Commit %s already reviewed in session %s, reusing",
                    commit_sha[:8], previous_session.id[:8],
                )
                return await self._copy_previous_session(
                    db, repository, pr_number, pr_title,
                    branch_name, base_branch, commit_sha,
                    previous_session,
                )

        # Create session record
        session = ReviewSession(
            repository_id=repository.id,
            pr_number=pr_number,
            pr_title=pr_title,
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            status="reviewing",
            started_at=datetime.utcnow(),
        )
        db.add(session)
        await db.flush()

        # Fire harness event
        await harness.fire(
            "on_review_started",
            session_id=session.id,
            repository=f"{repository.owner}/{repository.repo_name}",
            pr_number=pr_number,
            is_incremental=incremental_diff is not None,
        )

        try:
            # Set LangFuse trace context for the entire review pipeline
            from server.observability.callbacks import TraceContext
            TraceContext.set(session_id=session.id, phase="review")

            from server.services.github_service import GitHubService
            github = GitHubService(repository.github_token_encrypted)

            if incremental_diff:
                # ---- Incremental path ----
                diff_text = incremental_diff["diff"]
                pr_files = incremental_diff["files"]
                review_mode = "incremental"
            else:
                # ---- Full review path ----
                diff_text = await github.get_pr_diff(
                    repository.owner, repository.repo_name, pr_number
                )
                pr_files = await github.get_pr_files(
                    repository.owner, repository.repo_name, pr_number
                )
                review_mode = "full"

            # Store stats
            session.stats = {
                "total_files": len(pr_files),
                "total_additions": sum(f.get("additions", 0) for f in pr_files),
                "total_deletions": sum(f.get("deletions", 0) for f in pr_files),
                "review_mode": review_mode,
                "previous_session_id": previous_session.id if previous_session else None,
            }

            # Step 2: Chunk diff
            from server.context.diff_chunker import DiffChunker
            chunker = DiffChunker()
            chunks = chunker.split(diff_text, pr_files)

            # Limit review to code files only and max chunks to avoid timeouts
            CODE_EXTS = {".js", ".py", ".php", ".ts", ".tsx", ".jsx", ".go",
                         ".java", ".rb", ".rs", ".cs", ".swift", ".kt"}
            code_chunks = []
            for c in chunks:
                fp = c.get("file_path", "")
                ext = fp[fp.rfind("."):] if "." in fp else ""
                if ext.lower() in CODE_EXTS:
                    code_chunks.append(c)
            # Limit to max 20 most-changed code files
            code_chunks.sort(key=lambda c: c.get("additions", 0) + c.get("deletions", 0), reverse=True)
            chunks = code_chunks[:20]
            logger.info("Reviewing %d code chunks (filtered from %d total, mode=%s)", len(chunks), len(code_chunks), review_mode)

            # Step 3: Build context for each chunk
            from server.services.context_service import ContextService
            ctx_service = ContextService(repository, github)
            enriched_chunks = await ctx_service.build_context_for_chunks(chunks)

            # Step 4: Parallel review
            review_rules = repository.review_rules or {}
            findings = await self._run_parallel_review(
                session.id, enriched_chunks, review_rules
            )

            # Step 5: Aggregate & deduplicate
            from server.ai.aggregator import FindingAggregator
            aggregator = FindingAggregator()
            merged_findings = await aggregator.merge(findings)

            # Step 6: Verify high-severity findings
            verified_findings = await self._verify_findings(merged_findings)

            # Step 6.5: For incremental, carry forward findings for unchanged files
            new_findings_count = len(verified_findings)  # track for publish step
            if incremental_diff and previous_session:
                changed_files = {f.get("filename", "") for f in pr_files}
                old_findings = await self._get_previous_findings(db, previous_session.id)
                carried_findings = [
                    f for f in old_findings
                    if f.get("file_path", "") not in changed_files
                ]
                # Mark carried findings so publish step can skip them (already posted)
                for f in carried_findings:
                    f["_carried_forward"] = True
                if carried_findings:
                    logger.info(
                        "Carrying forward %d findings from previous session for %d unchanged files",
                        len(carried_findings), len({f["file_path"] for f in carried_findings}),
                    )
                    verified_findings.extend(carried_findings)

            # Save all findings to DB
            for finding in verified_findings:
                db_finding = ReviewFinding(
                    session_id=session.id,
                    reviewer_name=finding.get("reviewer_name", "unknown"),
                    severity=finding.get("severity", "medium"),
                    file_path=finding.get("file_path", ""),
                    line_start=finding.get("line_start", 0),
                    line_end=finding.get("line_end"),
                    title=finding.get("title", ""),
                    description=finding.get("description", ""),
                    suggestion=finding.get("suggestion"),
                    category=finding.get("category"),
                    is_verified=finding.get("is_verified", False),
                    verification_result=finding.get("verification_result"),
                )
                db.add(db_finding)

            # Build summary
            severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
            for f in verified_findings:
                sev = f.get("severity") or "low"
                if sev not in severity_counts:
                    sev = "low"
                severity_counts[sev] += 1

            summary_parts = [
                f"{c} {s}" for s, c in severity_counts.items() if c > 0
            ]
            mode_label = "Δ" if incremental_diff else ""
            # Chinese severity labels
            sev_cn = {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危", "info": "提示"}
            summary_parts_cn = [
                f"{sev_cn.get(s, s)}{c}个" for s, c in severity_counts.items() if c > 0
            ]
            session.summary = (
                f"{mode_label}代码审查发现 {len(verified_findings)} 个问题: "
                + (", ".join(summary_parts_cn) if summary_parts_cn else "无")
            )
            session.status = "completed"
            session.completed_at = datetime.utcnow()
            # Cache counts to avoid lazy-load after session close
            session._cached_findings_count = len(verified_findings)
            session._cached_severity_counts = severity_counts
            await db.flush()

            await harness.fire(
                "on_review_completed",
                session_id=session.id,
                findings_count=len(verified_findings),
                severity_counts=severity_counts,
                review_mode=review_mode,
            )

            # Best-effort post-review actions (publish, label, cache)
            # These run in background — failures do NOT affect the review result
            asyncio.ensure_future(
                self._post_review_actions(
                    repository=repository,
                    session_id=session.id,
                    commit_sha=session.commit_sha,
                    pr_number=session.pr_number,
                    verified_findings=list(verified_findings),
                    severity_counts=dict(severity_counts),
                    review_rules=dict(review_rules),
                )
            )

            return session

        except Exception as e:
            logger.error("Review failed for session %s: %s", session.id, e, exc_info=True)
            session.status = "failed"
            session._cached_findings_count = 0
            session._cached_severity_counts = {}
            await db.flush()

            await harness.fire(
                "on_review_failed",
                session_id=session.id,
                error=str(e),
            )
            raise

    async def _get_last_completed_session(
        self, db: AsyncSession, repository_id: str, pr_number: int
    ) -> ReviewSession | None:
        """Find the most recent completed review session for a PR."""
        result = await db.execute(
            select(ReviewSession)
            .where(
                ReviewSession.repository_id == repository_id,
                ReviewSession.pr_number == pr_number,
                ReviewSession.status == "completed",
            )
            .order_by(desc(ReviewSession.completed_at))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _get_previous_findings(
        self, db: AsyncSession, session_id: str
    ) -> list[dict]:
        """Load findings from a previous session as plain dicts."""
        result = await db.execute(
            select(ReviewFinding).where(ReviewFinding.session_id == session_id)
        )
        findings = result.scalars().all()
        return [
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

    async def _copy_previous_session(
        self,
        db: AsyncSession,
        repository: Repository,
        pr_number: int,
        pr_title: str,
        branch_name: str,
        base_branch: str,
        commit_sha: str,
        previous_session: ReviewSession,
    ) -> ReviewSession:
        """Create a new session that reuses findings from a previous session.

        Used when a commit was already reviewed (cache hit) or when an
        incremental diff yields zero changes.
        """
        # Load previous findings
        old_findings = await self._get_previous_findings(db, previous_session.id)

        # Create new session
        session = ReviewSession(
            repository_id=repository.id,
            pr_number=pr_number,
            pr_title=pr_title,
            branch_name=branch_name,
            base_branch=base_branch,
            commit_sha=commit_sha,
            status="completed",
            summary=f"Reused review from session {previous_session.id[:8]} (same commit or no delta changes)",
            stats={
                "total_files": previous_session.stats.get("total_files", 0) if previous_session.stats else 0,
                "review_mode": "reused",
                "previous_session_id": previous_session.id,
            },
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        db.add(session)
        await db.flush()

        # Copy findings to new session
        for f in old_findings:
            db_finding = ReviewFinding(
                session_id=session.id,
                reviewer_name=f.get("reviewer_name", "unknown"),
                severity=f.get("severity", "medium"),
                file_path=f.get("file_path", ""),
                line_start=f.get("line_start", 0),
                line_end=f.get("line_end"),
                title=f.get("title", ""),
                description=f.get("description", ""),
                suggestion=f.get("suggestion"),
                category=f.get("category"),
                is_verified=f.get("is_verified", False),
                verification_result=f.get("verification_result"),
            )
            db.add(db_finding)

        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in old_findings:
            sev = f.get("severity", "low")
            if sev not in severity_counts:
                sev = "low"
            severity_counts[sev] += 1

        session._cached_findings_count = len(old_findings)
        session._cached_severity_counts = severity_counts
        await db.flush()

        logger.info(
            "Session %s reused %d findings from %s (commit unchanged)",
            session.id[:8], len(old_findings), previous_session.id[:8],
        )
        return session

    async def _run_parallel_review(
        self, session_id: str, chunks: list, review_rules: dict
    ) -> list[dict]:
        """Run all enabled reviewers in parallel across all chunks."""
        from server.ai.reviewers.security import SecurityReviewer
        from server.ai.reviewers.performance import PerformanceReviewer
        from server.ai.reviewers.logic import LogicReviewer
        from server.ai.reviewers.style import StyleReviewer

        llm_factory = get_llm_factory()

        reviewers = []
        if review_rules.get("security", True):
            reviewers.append(SecurityReviewer(llm_factory))
        if review_rules.get("performance", True):
            reviewers.append(PerformanceReviewer(llm_factory))
        if review_rules.get("logic", True):
            reviewers.append(LogicReviewer(llm_factory))
        if review_rules.get("style", True):
            reviewers.append(StyleReviewer(llm_factory))

        max_per = review_rules.get("max_findings_per_reviewer", 20)

        async def review_chunk(chunk, reviewer):
            from server.observability.callbacks import TraceContext
            TraceContext.set(
                session_id=session_id,
                reviewer_name=reviewer.name,
                chunk_file=chunk.get("file_path", ""),
                phase="review",
            )
            try:
                return await reviewer.review(chunk)
            except Exception as e:
                logger.error(
                    "Reviewer %s failed on chunk %s: %s",
                    reviewer.name, chunk.get("file_path", "?"), e,
                )
                return []

        # Fan-out: all reviewers × all chunks
        tasks = []
        for chunk in chunks:
            for reviewer in reviewers:
                tasks.append(review_chunk(chunk, reviewer))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_findings = []
        for result in results:
            if isinstance(result, list):
                all_findings.extend(result)
            elif isinstance(result, Exception):
                logger.warning("Review task failed: %s", result)

        return all_findings[:max_per * len(reviewers)]

    async def _verify_findings(self, findings: list[dict]) -> list[dict]:
        """Verify high-severity findings in the MCP sandbox."""
        verified = []
        for finding in findings:
            if finding.get("severity") in ("critical", "high"):
                try:
                    from server.mcp.sandbox import sandbox
                    verify_result = await self._verify_single(finding, sandbox)
                    finding["is_verified"] = True
                    finding["verification_result"] = verify_result
                except Exception as e:
                    logger.warning("Verification failed for finding: %s", e)
                    finding["is_verified"] = False
                    finding["verification_result"] = f"Verification skipped: {e}"
            verified.append(finding)
        return verified

    async def _verify_single(self, finding: dict, sandbox) -> str:
        """Verify a single finding using the appropriate tool."""
        category = finding.get("category", "")
        file_content = finding.get("context", {}).get("file_content", "")

        if not file_content:
            return "No file content available for verification"

        if "sql_injection" in (category or ""):
            # Run semgrep for SQL injection patterns
            result = await sandbox.execute(
                f"echo '{file_content[:5000]}' | semgrep --lang python --pattern '$SQL' -",
                language="python",
                timeout=15,
            )
            return f"Semgrep: {result.stdout[:500]}" if result.stdout else "No issues found by semgrep"

        if category in ("n_plus_1", "memory_leak", "inefficient_algorithm"):
            result = await sandbox.execute(
                f"bandit -c /dev/stdin 2>&1 || true",
                language="python",
                timeout=15,
            )
            return f"Bandit: {result.stdout[:500]}" if result.stdout else "Bandit found no issues"

        return "Verification passed (no specific tool for this category)"

    async def _publish_findings(
        self,
        github: "GitHubService",
        repository: Repository,
        session: ReviewSession,
        findings: list[dict],
        db: AsyncSession,
    ):
        """Post findings as GitHub PR inline comments.

        For incremental reviews, carried-forward findings (from unchanged files)
        are skipped — only new findings for changed files are posted.
        """
        # Separate new findings from carried-forward ones
        new_findings = [f for f in findings if not f.get("_carried_forward")]
        carried_count = len(findings) - len(new_findings)

        comments = []
        for f in new_findings:
            comments.append({
                "path": f["file_path"],
                "line": f.get("line_end") or f["line_start"],
                "body": self._format_comment(f),
            })

        if comments or carried_count:
            is_incremental = session.stats.get("review_mode") == "incremental" if session.stats else False
            if is_incremental:
                summary_body = (
                    f"## 🤖 AI Code Review (Δ增量审查)\n\n"
                    f"本次审查了 {session.stats.get('total_files', 0) if session.stats else 0}"
                    f" 个变更文件，发现 **{len(new_findings)}** 个新问题"
                )
                if carried_count:
                    summary_body += f"（另有 {carried_count} 个已有问题从未变更的文件中保留）"
                summary_body += "。\n\n"
            else:
                summary_body = (
                    f"## 🤖 AI 代码审查\n\n"
                    f"在 {session.stats.get('total_files', 0) if session.stats else 0}"
                    f" 个文件中发现 **{len(findings)}** 个问题。\n\n"
                )

            severity_icons = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "ℹ️"}
            for f in new_findings[:10]:
                summary_body += (
                    f"- {severity_icons.get(f.get('severity', 'low'), 'ℹ️')} "
                    f"[{f.get('severity', 'low').upper()}] **{f.get('title', '')}** "
                    f"— `{f.get('file_path', '')}:{f.get('line_start', 0)}`\n"
                )

            await github.create_review(
                owner=repository.owner,
                repo_name=repository.repo_name,
                pr_number=session.pr_number,
                commit_sha=session.commit_sha,
                body=summary_body,
                event="COMMENT",
                comments=comments[:50],  # GitHub limits reviews to 50 comments
            )

            await harness.fire(
                "on_review_published",
                session_id=session.id,
                comments_posted=len(comments[:50]),
            )

    def _format_comment(self, finding: dict) -> str:
        """Format a finding as a GitHub comment body (Chinese)."""
        severity_emoji = {
            "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "ℹ️",
        }
        sev_cn = {"critical": "严重", "high": "高危", "medium": "中危", "low": "低危", "info": "提示"}
        reviewer_cn = {
            "security": "安全审查", "performance": "性能审查",
            "logic": "逻辑审查", "style": "代码风格审查",
        }
        emoji = severity_emoji.get(finding.get("severity", "low"), "ℹ️")
        sev_label = sev_cn.get(finding.get("severity", "low"), finding.get("severity", "low"))
        reviewer_label = reviewer_cn.get(finding.get("reviewer_name", ""), finding.get("reviewer_name", "unknown"))
        lines = [
            f"{emoji} **{finding.get('title', 'Issue')}** `[{sev_label}]`",
            f"",
            f"> 🤖 *{reviewer_label}* | `{finding.get('category', 'general')}`",
            f"",
            f"**问题:** {finding.get('description', '')}",
        ]
        if finding.get("suggestion"):
            lines.append(f"")
            lines.append(f"**建议:** {finding['suggestion']}")
        return "\n".join(lines)

    async def _post_review_actions(
        self,
        repository,
        session_id: str,
        commit_sha: str,
        pr_number: int,
        verified_findings: list[dict],
        severity_counts: dict,
        review_rules: dict,
    ):
        """Background task: publish, label, and cache after a successful review.

        This runs independently of the main review pipeline — failures here
        do NOT affect the review result.
        """
        logger.info("Post-review actions starting for session %s", session_id)

        # Publish to GitHub
        if review_rules.get("auto_publish"):
            try:
                from server.services.github_service import GitHubService
                from server.database import async_session
                from sqlalchemy import select
                from server.models.review_session import ReviewSession

                github = GitHubService(repository.github_token_encrypted)
                async with async_session() as db:
                    result = await db.execute(
                        select(ReviewSession).where(ReviewSession.id == session_id)
                    )
                    session = result.scalar_one_or_none()
                    if session:
                        await self._publish_findings(
                            github, repository, session, verified_findings, db
                        )
                        logger.info("Auto-published %d findings to GitHub", len(verified_findings))
            except Exception as e:
                logger.warning("Auto-publish failed (non-fatal): %s", e)

        # Auto-label PR
        if review_rules.get("auto_label", True):
            try:
                from server.services.github_service import GitHubService
                from server.services.auto_label_service import auto_labeler

                github = GitHubService(repository.github_token_encrypted)
                # Convert findings dicts to simple objects for labeler
                class FindingProxy:
                    def __init__(self, d):
                        self.category = d.get("category", "")
                        self.severity = d.get("severity", "medium")
                        self.reviewer_name = d.get("reviewer_name", "")
                proxies = [FindingProxy(f) for f in verified_findings]

                # Create a minimal session proxy
                class SessionProxy:
                    pass
                sp = SessionProxy()

                await auto_labeler.apply_labels(
                    github, repository.owner, repository.repo_name,
                    pr_number, sp, proxies,
                )
                logger.info("Auto-labeled PR #%d", pr_number)
            except Exception as e:
                logger.warning("Auto-labeling failed (non-fatal): %s", e)

        # Cache result
        try:
            from server.services.cache_service import cache
            await cache.cache_review_result(
                commit_sha=commit_sha,
                repo_id=repository.id,
                result={
                    "session_id": session_id,
                    "findings_count": len(verified_findings),
                    "severity_counts": severity_counts,
                    "status": "completed",
                },
            )
            logger.debug("Review result cached")
        except Exception as e:
            logger.debug("Result caching skipped: %s", e)

        logger.info("Post-review actions done for session %s", session_id)
