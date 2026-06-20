"""Model router — routes review tasks to appropriate models.

Security/Logic/Performance → GPT-4o / Claude (large, high accuracy)
Style → GPT-4o-mini (small, cheap)
Aggregation/Arbitration → GPT-4o (single call, low volume)

With automatic fallback on timeout/error.
"""
import logging

from server.config import settings
from server.ai.llm import LLMFactory

logger = logging.getLogger(__name__)


class ModelRouter:
    """Routes review dimensions to the optimal model based on task criticality."""

    # High-criticality reviewers use the main (large) model
    CRITICAL_REVIEWERS = {"security", "performance", "logic", "aggregator"}

    # Low-criticality reviewers use the small (cheap) model
    ECONOMY_REVIEWERS = {"style"}

    def __init__(self, llm_factory: LLMFactory):
        self.llm_factory = llm_factory

    def get_model_for(self, reviewer_name: str) -> str:
        """Get the model name for a given reviewer."""
        if reviewer_name in self.ECONOMY_REVIEWERS:
            return settings.llm_small_model
        return settings.llm_model

    def get_temperature_for(self, reviewer_name: str) -> float:
        """Get temperature for a given reviewer.

        Security needs deterministic output (low temp).
        Style can be more creative (slightly higher temp).
        """
        temps = {
            "security": 0.1,
            "performance": 0.2,
            "logic": 0.2,
            "style": 0.4,
            "aggregator": 0.3,
        }
        return temps.get(reviewer_name, 0.2)

    def get_chat_model(
        self,
        reviewer_name: str,
        streaming: bool = False,
        callbacks: list | None = None,
    ):
        """Get a ChatOpenAI instance routed to the appropriate model.

        Includes automatic fallback: if the primary model fails, retry with
        the other model (e.g., gpt-4o → gpt-4o-mini and vice versa).
        """
        primary_model = self.get_model_for(reviewer_name)
        temperature = self.get_temperature_for(reviewer_name)

        try:
            return self.llm_factory.get_chat_model(
                temperature=temperature,
                streaming=streaming,
                model=primary_model,
                callbacks=callbacks,
            )
        except Exception as e:
            logger.warning(
                "Primary model %s unavailable for %s: %s. Falling back.",
                primary_model, reviewer_name, e,
            )
            fallback_model = (
                settings.llm_small_model
                if primary_model == settings.llm_model
                else settings.llm_model
            )
            return self.llm_factory.get_chat_model(
                temperature=temperature,
                streaming=streaming,
                model=fallback_model,
                callbacks=callbacks,
            )
