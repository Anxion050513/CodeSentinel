"""Multi-agent result aggregator — dedup, merge, sort, and arbitrate findings."""
import hashlib
import json
import logging

from server.ai.llm import LLMFactory
from server.ai.model_router import ModelRouter

logger = logging.getLogger(__name__)

# Severity ordering for sorting
SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


class FindingAggregator:
    """Aggregates findings from multiple reviewers:

    1. Deduplication — same line, same issue → keep only the highest-severity one
    2. Sorting — by severity (critical first), then by file path
    3. Conflict resolution — when two reviewers disagree on the same line, LLM arbitrates
    """

    def __init__(self, llm_factory: LLMFactory | None = None):
        self.llm_factory = llm_factory
        if llm_factory:
            self.router = ModelRouter(llm_factory)
        else:
            self.router = None

    async def merge(self, findings: list[dict]) -> list[dict]:
        """Merge, deduplicate, sort, and arbitrate findings."""
        if not findings:
            return []

        # Step 1: Deduplicate by file + line + category fingerprint
        deduped = self._deduplicate(findings)

        # Step 2: Detect and resolve conflicts
        resolved = await self._resolve_conflicts(deduped)

        # Step 3: Sort by severity
        resolved.sort(key=lambda f: (
            SEVERITY_ORDER.get(f.get("severity", "low"), 99),
            f.get("file_path", ""),
            f.get("line_start", 0),
        ))

        return resolved

    def _deduplicate(self, findings: list[dict]) -> list[dict]:
        """Deduplicate findings by (file_path, line_start, category)."""
        seen = {}
        for finding in findings:
            key = self._fingerprint(finding)
            if key in seen:
                existing = seen[key]
                # Keep the finding with the higher severity
                if (
                    SEVERITY_ORDER.get(finding.get("severity"), 99)
                    < SEVERITY_ORDER.get(existing.get("severity"), 99)
                ):
                    seen[key] = finding
                # If same severity, keep the more detailed description
                elif (
                    finding.get("severity") == existing.get("severity")
                    and len(finding.get("description", ""))
                    > len(existing.get("description", ""))
                ):
                    seen[key] = finding
            else:
                seen[key] = finding
        return list(seen.values())

    def _fingerprint(self, finding: dict) -> str:
        """Create a stable fingerprint for deduplication.

        Same file + line + category = same issue.
        """
        parts = [
            finding.get("file_path") or "",
            str(finding.get("line_start") or 0),
            finding.get("category") or "",
        ]
        return hashlib.md5("|".join(parts).encode()).hexdigest()

    async def _resolve_conflicts(self, findings: list[dict]) -> list[dict]:
        """Detect and resolve conflicts where two reviewers disagree on the same line.

        When two findings target the same line but have different conclusions,
        use an LLM to arbitrate.
        """
        # Group by (file, line)
        by_line: dict[str, list[dict]] = {}
        for f in findings:
            key = f"{f.get('file_path', '')}:{f.get('line_start', 0)}"
            by_line.setdefault(key, []).append(f)

        conflicts = {k: v for k, v in by_line.items() if len(v) > 1}
        if not conflicts:
            return findings

        # For conflicts where different reviewers report the same issue, keep all
        # (it means multiple perspectives agree there's a problem)
        # For truly contradictory findings (one says X, another says not-X),
        # use LLM arbitration if available
        resolved = []
        conflict_keys_to_resolve = []

        for line_key, group in conflicts.items():
            categories = {f.get("category") for f in group}
            if len(categories) > 1:
                # Different categories on same line — keep all (multi-faceted issue)
                resolved.extend(group)
            elif len({f.get("title", "") for f in group}) > 1:
                # Same line, same category, but different titles — flag for arbitration
                conflict_keys_to_resolve.append(line_key)
            else:
                # Same issue, keep one
                resolved.append(group[0])

        # For remaining conflicts, use LLM arbitration if available
        for line_key in conflict_keys_to_resolve:
            group = by_line[line_key]
            if self.router:
                try:
                    winner = await self._arbitrate(group)
                    if winner:
                        resolved.append(winner)
                    else:
                        # If arbitration fails, keep the highest-severity one
                        resolved.extend(group)
                except Exception as e:
                    logger.debug("Arbitration failed for %s: %s", line_key, e)
                    resolved.extend(group)
            else:
                resolved.extend(group)

        return resolved

    async def _arbitrate(self, findings: list[dict]) -> dict | None:
        """Use LLM to arbitrate between conflicting findings on the same line.

        Returns the 'winning' finding, or None if both should be kept.
        """
        if not self.router:
            return None

        from server.observability.callbacks import TraceContext
        TraceContext.set(phase="arbitrate")

        chat = self.router.get_chat_model("aggregator")

        message = (
            "You are reviewing conflicting code review findings on the same line.\n\n"
            "## Conflicting Findings\n"
        )
        for i, f in enumerate(findings, 1):
            message += (
                f"**Finding {i}** (by {f.get('reviewer_name', '?')}):\n"
                f"- Title: {f.get('title', '')}\n"
                f"- Severity: {f.get('severity', 'medium')}\n"
                f"- Description: {f.get('description', '')}\n"
                f"- Suggestion: {f.get('suggestion', 'N/A')}\n\n"
            )

        message += (
            "Are these findings:\n"
            "1. The same issue (different wording) → respond with the index of the best explanation (1 or 2)\n"
            "2. Different issues on the same line → respond with 0 to keep both\n\n"
            "Respond with just the number (0, 1, or 2)."
        )

        try:
            response = await chat.ainvoke(message)
            choice = response.content.strip()
            if choice in ("1", "2"):
                idx = int(choice) - 1
                return findings[idx]
        except Exception:
            pass

        return None  # Keep both if arbitration fails
