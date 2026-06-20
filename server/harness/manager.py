"""HarnessManager — central event system using pluggy.

Reused and adapted from the interview system's server/harness/manager.py.
Only the namespace and hook spec class changed; the core logic is identical.
"""
import asyncio
import logging
from typing import Any

import pluggy

from server.harness.hooks import ReviewHooks
from server.harness.plugins.logger_plugin import LoggerPlugin
from server.harness.plugins.metrics_plugin import MetricsPlugin
from server.harness.plugins.notification_plugin import NotificationPlugin

logger = logging.getLogger(__name__)


class HarnessManager:
    """Manages the pluggy plugin system and fires lifecycle events."""

    def __init__(self):
        self.pm = pluggy.PluginManager("ai_code_reviewer")
        self.pm.add_hookspecs(ReviewHooks)
        self._register_builtin_plugins()

    def _register_builtin_plugins(self):
        """Register default harness plugins."""
        self.register(LoggerPlugin())
        self.register(MetricsPlugin())
        self.register(NotificationPlugin())
        logger.info("Built-in harness plugins registered (3 plugins)")

    def register(self, plugin: Any) -> None:
        """Register a custom plugin instance."""
        self.pm.register(plugin)
        logger.info(f"Plugin registered: {plugin.__class__.__name__}")

    def unregister(self, plugin: Any) -> None:
        """Unregister a plugin."""
        self.pm.unregister(plugin)

    async def fire(self, hook_name: str, **kwargs) -> Any:
        """Fire a hook event to all registered plugins.

        pluggy returns a list of coroutines for async hooks;
        we gather them all and return results.
        For firstresult=True hooks, pluggy returns the first non-None result directly.
        """
        try:
            hook = getattr(self.pm.hook, hook_name)
            results = hook(**kwargs)
            # pluggy async hooks return a list of coroutines
            if isinstance(results, list) and results and asyncio.iscoroutine(results[0]):
                return await asyncio.gather(*results)
            if asyncio.iscoroutine(results):
                return await results
            return results
        except Exception as e:
            logger.error(f"Error firing hook '{hook_name}': {e}")
            return None


# Global singleton
harness = HarnessManager()
