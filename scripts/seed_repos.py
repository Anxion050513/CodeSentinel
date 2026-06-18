"""Seed script — import example repositories into the database.

Usage:
    python -m scripts.seed_repos
"""
import asyncio
import json
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.config import settings
from server.database import async_session_factory
from server.models.repository import Repository


EXAMPLE_REPOS = [
    {
        "owner": "example",
        "repo_name": "flask-api",
        "webhook_secret": "whsec_dev_example",
        "github_token": "ghp_dev_example_token",
        "review_rules": {
            "security": True,
            "performance": True,
            "logic": True,
            "style": True,
            "min_severity": "low",
            "auto_publish": False,
            "max_findings_per_reviewer": 20,
        },
    },
    {
        "owner": "example",
        "repo_name": "django-app",
        "webhook_secret": "whsec_dev_django",
        "github_token": "ghp_dev_django_token",
        "review_rules": {
            "security": True,
            "performance": True,
            "logic": True,
            "style": False,
            "min_severity": "medium",
            "auto_publish": True,
            "max_findings_per_reviewer": 15,
        },
    },
]


async def seed():
    """Seed the database with example repositories."""
    async with async_session_factory() as db:
        for repo_data in EXAMPLE_REPOS:
            # Check if already exists
            from sqlalchemy import select
            result = await db.execute(
                select(Repository).where(
                    Repository.owner == repo_data["owner"],
                    Repository.repo_name == repo_data["repo_name"],
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                print(f"Repository {repo_data['owner']}/{repo_data['repo_name']} already exists, skipping")
                continue

            repo = Repository(
                owner=repo_data["owner"],
                repo_name=repo_data["repo_name"],
                webhook_secret=repo_data["webhook_secret"],
                github_token_encrypted=repo_data["github_token"],
                review_rules=repo_data["review_rules"],
                is_active=True,
            )
            db.add(repo)
            print(f"Seeded: {repo_data['owner']}/{repo_data['repo_name']}")

        await db.commit()
        print(f"\nDone! Seeded {len(EXAMPLE_REPOS)} repositories.")


if __name__ == "__main__":
    asyncio.run(seed())
