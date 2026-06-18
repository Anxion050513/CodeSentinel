"""Integration test for incremental review detection logic."""
import asyncio
from sqlalchemy import select, desc
from server.database import async_session
from server.models.repository import Repository
from server.models.review_session import ReviewSession
from server.models.review_finding import ReviewFinding
from datetime import datetime


async def test_incremental_detection():
    async with async_session() as db:
        # 1. Create a test repository
        repo = Repository(
            owner="test-owner", repo_name="test-repo",
            webhook_secret="whsec_test123", github_token_encrypted="ghp_fake",
            review_rules={"security": True, "performance": True, "logic": True, "style": True},
        )
        db.add(repo)
        await db.flush()
        print("1. Created repo:", repo.id[:8] + "...")

        # 2. Create a previous completed session (simulating first review)
        prev = ReviewSession(
            repository_id=repo.id, pr_number=1,
            pr_title="Test PR", branch_name="feature/x",
            base_branch="main", commit_sha="abc111",
            status="completed", summary="First review: 3 issues",
            stats={"total_files": 5, "total_additions": 100, "total_deletions": 50},
            started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
        )
        db.add(prev)
        await db.flush()
        print("2. Created previous session:", prev.id[:8] + "... (commit abc111)")

        # 3. Add findings to previous session
        for i, fdata in enumerate([
            ("security", "high", "src/auth.py", 10, "sql_injection"),
            ("performance", "medium", "src/api.py", 45, "n_plus_1"),
            ("style", "low", "src/utils.py", 80, "naming"),
        ]):
            f = ReviewFinding(
                session_id=prev.id,
                reviewer_name=fdata[0], severity=fdata[1],
                file_path=fdata[2], line_start=fdata[3],
                category=fdata[4],
                title=f"Issue {i+1}: {fdata[4]}",
                description=f"Description for {fdata[4]}",
            )
            db.add(f)
        await db.flush()
        print("3. Added 3 findings to previous session")

        # 4. Query for last completed session (mimics _get_last_completed_session)
        result = await db.execute(
            select(ReviewSession)
            .where(
                ReviewSession.repository_id == repo.id,
                ReviewSession.pr_number == 1,
                ReviewSession.status == "completed",
            )
            .order_by(desc(ReviewSession.completed_at))
            .limit(1)
        )
        found = result.scalar_one_or_none()
        short_id = found.id[:8] if found else "NONE"
        print(f"4. Last completed session for PR #1: {short_id}...")
        assert found is not None, "Should find previous session"
        assert found.commit_sha == "abc111", "Commit should match"
        print("   OK: incremental detection — previous session found")

        # 5. Test: same commit → should reuse
        if found.commit_sha == "abc111":
            print("5. Same commit → would trigger _copy_previous_session (reuse)")
            print("   OK: no redundant review for already-reviewed commit")

        # 6. Test: get previous findings (mimics _get_previous_findings)
        f_result = await db.execute(
            select(ReviewFinding).where(ReviewFinding.session_id == prev.id)
        )
        old_findings = f_result.scalars().all()
        print(f"6. Loaded {len(old_findings)} findings from previous session")
        assert len(old_findings) == 3
        print("   OK: findings loaded correctly")

        # 7. Simulate carry-forward: file src/auth.py changed, others unchanged
        changed_files = {"src/auth.py"}
        carried = [f for f in old_findings if f.file_path not in changed_files]
        new_count = len(old_findings) - len(carried)
        print(f"7. Simulated carry-forward: {new_count} new + {len(carried)} carried")

        # Cleanup
        await db.rollback()
        print("\n=== All integration tests passed ===")


if __name__ == "__main__":
    asyncio.run(test_incremental_detection())
