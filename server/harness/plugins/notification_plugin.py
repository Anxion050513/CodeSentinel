"""Notification plugin — sends review results to IM platforms.

Supports:
- DingTalk (钉钉) webhook
- Feishu/Lark (飞书) webhook
- WeCom/WeChat Work (企业微信) webhook

Configure via environment variables or review_rules in repository settings.
"""
import hashlib
import json
import logging
import time

import httpx

from server.config import settings

logger = logging.getLogger("ai_code_reviewer.notifications")


class NotificationPlugin:
    """Sends review lifecycle notifications to configured IM platforms.

    Each platform can be configured independently via:
    - DINGTALK_WEBHOOK_URL + DINGTALK_SECRET
    - FEISHU_WEBHOOK_URL + FEISHU_SECRET
    - WECOM_WEBHOOK_URL
    """

    PLATFORM_CONFIGS = {
        "dingtalk": {
            "env_url": "DINGTALK_WEBHOOK_URL",
            "env_secret": "DINGTALK_SECRET",
        },
        "feishu": {
            "env_url": "FEISHU_WEBHOOK_URL",
            "env_secret": "FEISHU_SECRET",
        },
        "wecom": {
            "env_url": "WECOM_WEBHOOK_URL",
        },
    }

    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15)
        return self._client

    def _get_platform_config(self, platform: str) -> tuple[str | None, str | None]:
        """Get webhook URL and secret for a platform from env."""
        config = self.PLATFORM_CONFIGS.get(platform, {})
        url = getattr(settings, config.get("env_url", "").lower(), None) or ""
        secret = getattr(settings, config.get("env_secret", "").lower(), None) or ""
        return (url or None, secret or None)

    # ---- DingTalk (钉钉) ----

    async def send_dingtalk(self, title: str, text: str, url: str | None = None, secret: str | None = None):
        """Send a markdown message to DingTalk."""
        webhook_url = url or self._get_platform_config("dingtalk")[0]
        secret = secret or self._get_platform_config("dingtalk")[1]

        if not webhook_url:
            logger.debug("DingTalk not configured, skipping notification")
            return

        # Sign the request if secret is provided
        final_url = webhook_url
        if secret:
            timestamp = str(round(time.time() * 1000))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hashlib.sha256(
                string_to_sign.encode("utf-8")
            ).digest()
            import base64
            sign = base64.b64encode(hmac_code).decode("utf-8")
            final_url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text,
            },
        }

        try:
            resp = await self.client.post(final_url, json=payload)
            resp.raise_for_status()
            logger.info("DingTalk notification sent: %s", title)
        except Exception as e:
            logger.warning("DingTalk notification failed: %s", e)

    # ---- Feishu/Lark (飞书) ----

    async def send_feishu(self, title: str, text: str, url: str | None = None, secret: str | None = None):
        """Send a card message to Feishu."""
        webhook_url = url or self._get_platform_config("feishu")[0]
        secret = secret or self._get_platform_config("feishu")[1]

        if not webhook_url:
            logger.debug("Feishu not configured, skipping notification")
            return

        # Feishu uses interactive card format
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title,
                    },
                    "template": "red" if "critical" in text.lower() else "blue",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": text,
                    },
                ],
            },
        }

        # Sign with timestamp if secret provided
        if secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{secret}"
            hmac_code = hashlib.sha256(
                string_to_sign.encode("utf-8")
            ).digest()
            import base64
            sign = base64.b64encode(hmac_code).decode("utf-8")
            payload["timestamp"] = timestamp
            payload["sign"] = sign

        try:
            resp = await self.client.post(webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("Feishu notification sent: %s", title)
        except Exception as e:
            logger.warning("Feishu notification failed: %s", e)

    # ---- WeCom/WeChat Work (企业微信) ----

    async def send_wecom(self, title: str, text: str, url: str | None = None):
        """Send a markdown message to WeCom."""
        webhook_url = url or self._get_platform_config("wecom")[0]

        if not webhook_url:
            logger.debug("WeCom not configured, skipping notification")
            return

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## {title}\n\n{text}",
            },
        }

        try:
            resp = await self.client.post(webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("WeCom notification sent: %s", title)
        except Exception as e:
            logger.warning("WeCom notification failed: %s", e)

    # ---- Unified send ----

    async def send_all(self, title: str, text: str, platforms: list[str] | None = None):
        """Send notification to all configured platforms."""
        if platforms is None:
            platforms = list(self.PLATFORM_CONFIGS.keys())

        senders = {
            "dingtalk": self.send_dingtalk,
            "feishu": self.send_feishu,
            "wecom": self.send_wecom,
        }

        for platform in platforms:
            sender = senders.get(platform)
            if sender:
                try:
                    await sender(title, text)
                except Exception as e:
                    logger.warning("Notification to %s failed: %s", platform, e)

    # ---- Harness hook implementations ----

    async def on_review_completed(
        self, session_id: str, findings_count: int, severity_counts: dict
    ):
        """Send notification when a review completes."""
        if findings_count == 0:
            return  # No findings, skip notification

        critical = severity_counts.get("critical", 0)
        high = severity_counts.get("high", 0)
        medium = severity_counts.get("medium", 0)
        low = severity_counts.get("low", 0)

        title = f"🤖 Code Review Complete — {findings_count} issue(s) found"
        text = (
            f"**Session:** `{session_id[:8]}...`\n\n"
            f"**Findings:** {findings_count} total\n"
            f"- 🔴 Critical: {critical}\n"
            f"- 🟠 High: {high}\n"
            f"- 🟡 Medium: {medium}\n"
            f"- 🟢 Low: {low}\n\n"
            f"View details at the admin dashboard."
        )

        await self.send_all(title, text)

    async def on_review_failed(self, session_id: str, error: str):
        """Send notification when a review fails."""
        title = f"⚠️ Code Review Failed — `{session_id[:8]}...`"
        text = (
            f"**Session:** `{session_id}`\n\n"
            f"**Error:** {error[:200]}\n\n"
            f"The review will be automatically retried if configured."
        )

        await self.send_all(title, text)

    async def on_finding_detected(
        self, session_id: str, reviewer_name: str,
        severity: str, category: str, file_path: str, line_start: int
    ):
        """Optionally send real-time alerts for critical findings."""
        if severity != "critical":
            return

        title = f"🔴 Critical Issue: {category}"
        text = (
            f"**Reviewer:** {reviewer_name}\n"
            f"**Location:** `{file_path}:{line_start}`\n"
            f"**Category:** {category}\n"
            f"**Session:** `{session_id[:8]}...`\n\n"
            f"Immediate attention recommended."
        )

        await self.send_all(title, text)

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


# Singleton
notifier = NotificationPlugin()