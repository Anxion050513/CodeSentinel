"""GitHub App installation service — exchange installation tokens.

Handles the GitHub App authentication flow:
1. Generate JWT from app private key
2. Exchange JWT for installation access token
3. Token refresh and caching
"""
import logging
import os
import time

import httpx

from server.config import settings

# cryptography is optional — only needed for GitHub App JWT signing
try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubAppService:
    """Manages GitHub App authentication and installation tokens.

    Implements the full GitHub App auth flow:
    - Generate a JWT signed with the app's private key
    - Use the JWT to request an installation access token
    - Cache tokens until expiry (1 hour by default)
    """

    def __init__(self):
        self._private_key = None
        self._token_cache: dict[int, dict] = {}  # installation_id -> {token, expires_at}

    @property
    def private_key(self):
        """Load the GitHub App private key from disk."""
        if self._private_key is not None:
            return self._private_key

        if not HAS_CRYPTO:
            logger.warning(
                "cryptography package not installed. "
                "Install with: pip install cryptography PyJWT"
            )
            return None

        key_path = settings.github_app_private_key_path
        if not os.path.exists(key_path):
            logger.warning("GitHub App private key not found at %s", key_path)
            return None

        try:
            with open(key_path, "rb") as f:
                key_data = f.read()
                self._private_key = serialization.load_ssh_private_key(
                    key_data,
                    password=None,
                    backend=default_backend(),
                )
            logger.info("GitHub App private key loaded from %s", key_path)
            return self._private_key
        except Exception as e:
            logger.error("Failed to load GitHub App private key: %s", e)
            return None

    async def _generate_jwt(self) -> str | None:
        """Generate a JWT signed with the GitHub App's private key."""
        key = self.private_key
        if key is None:
            return None

        import jwt

        now = int(time.time())
        payload = {
            "iat": now - 60,       # issued at (60s leeway for clock drift)
            "exp": now + 600,      # expires in 10 minutes (max)
            "iss": settings.github_app_id,
        }

        try:
            token = jwt.encode(payload, key, algorithm="RS256")
            return token
        except Exception as e:
            logger.error("JWT generation failed: %s", e)
            return None

    async def get_installation_token(
        self, installation_id: int, force_refresh: bool = False
    ) -> str | None:
        """Get a fresh installation access token.

        Tokens are cached for up to 55 minutes (GitHub tokens last 1 hour).

        Args:
            installation_id: The GitHub App installation ID
            force_refresh: If True, force a new token even if cached

        Returns:
            Installation access token string, or None on failure
        """
        # Check cache
        if not force_refresh and installation_id in self._token_cache:
            cached = self._token_cache[installation_id]
            if time.time() < cached.get("expires_at", 0) - 300:  # 5 min buffer
                logger.debug("Using cached token for installation %d", installation_id)
                return cached["token"]

        # Generate JWT
        jwt_token = await self._generate_jwt()
        if not jwt_token:
            logger.error("Failed to generate JWT for installation %d", installation_id)
            return None

        # Exchange JWT for installation token
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-code-review-bot/0.1",
        }

        url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=headers)
                response.raise_for_status()
                data = response.json()

                token = data.get("token")
                expires_at_str = data.get("expires_at", "")

                if token:
                    # Parse expiry (GitHub returns ISO 8601)
                    from datetime import datetime
                    try:
                        expires_at = datetime.fromisoformat(
                            expires_at_str.replace("Z", "+00:00")
                        ).timestamp()
                    except (ValueError, AttributeError):
                        expires_at = time.time() + 3600  # Default: 1 hour

                    self._token_cache[installation_id] = {
                        "token": token,
                        "expires_at": expires_at,
                        "repositories": data.get("repository_selection", "selected"),
                    }

                    logger.info(
                        "Token acquired for installation %d (expires in %.0fs)",
                        installation_id, expires_at - time.time(),
                    )
                    return token
                else:
                    logger.error("No token in response for installation %d", installation_id)
                    return None

        except httpx.HTTPStatusError as e:
            logger.error(
                "GitHub API error for installation %d: %s %s",
                installation_id, e.response.status_code, e.response.text[:200],
            )
            return None
        except Exception as e:
            logger.error(
                "Failed to get installation token for %d: %s",
                installation_id, e,
            )
            return None

    async def list_installations(self) -> list[dict]:
        """List all installations for this GitHub App."""
        jwt_token = await self._generate_jwt()
        if not jwt_token:
            return []

        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-code-review-bot/0.1",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{GITHUB_API}/app/installations",
                    headers=headers,
                )
                response.raise_for_status()
                installations = response.json()

                return [
                    {
                        "id": inst.get("id"),
                        "account": inst.get("account", {}).get("login", ""),
                        "account_type": inst.get("account", {}).get("type", ""),
                        "repository_selection": inst.get("repository_selection", ""),
                        "html_url": inst.get("html_url", ""),
                    }
                    for inst in installations
                ]
        except Exception as e:
            logger.error("Failed to list installations: %s", e)
            return []

    async def list_installation_repos(
        self, installation_id: int
    ) -> list[dict]:
        """List repositories accessible to an installation."""
        token = await self.get_installation_token(installation_id)
        if not token:
            return []

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "ai-code-review-bot/0.1",
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{GITHUB_API}/installation/repositories",
                    headers=headers,
                    params={"per_page": 100},
                )
                response.raise_for_status()
                data = response.json()

                return [
                    {
                        "id": r.get("id"),
                        "full_name": r.get("full_name", ""),
                        "private": r.get("private", False),
                        "default_branch": r.get("default_branch", "main"),
                    }
                    for r in data.get("repositories", [])
                ]
        except Exception as e:
            logger.error(
                "Failed to list repos for installation %d: %s",
                installation_id, e,
            )
            return []

    def invalidate_cache(self, installation_id: int | None = None):
        """Clear cached tokens."""
        if installation_id is not None:
            self._token_cache.pop(installation_id, None)
        else:
            self._token_cache.clear()
            logger.info("All installation token caches cleared")


# Singleton
github_app = GitHubAppService()