"""Eval framework — run golden dataset through reviewer agents.

Measures precision (low false positives) and recall (low false negatives)
against a golden dataset of human-annotated code review findings.

Adapted from the interview system's eval_runner.py pattern.
"""
import json
import logging
import os
import statistics
from typing import Optional

from pydantic import BaseModel, Field

from server.ai.llm import LLMFactory

logger = logging.getLogger(__name__)


# === Pydantic schemas ===

class EvalTestResult(BaseModel):
    test_id: str
    description: str = ""
    expected_count: int = 0
    actual_count: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    details: list[dict] = Field(default_factory=list)


class EvalReport(BaseModel):
    total_tests: int
    avg_precision: float
    avg_recall: float
    avg_f1: float
    results: list[EvalTestResult]
    summary: str


# === Eval Runner ===

class CodeReviewEvalRunner:
    """Runs the golden dataset through reviewer agents and computes metrics."""

    CATEGORY_MAPPING = {
        "sql_injection": "security",
        "xss": "security",
        "hardcoded_secret": "security",
        "insecure_auth": "security",
        "path_traversal": "security",
        "command_injection": "security",
        "insecure_crypto": "security",
        "n_plus_1": "performance",
        "memory_leak": "performance",
        "inefficient_algorithm": "performance",
        "null_pointer": "logic",
        "boundary": "logic",
        "exception_handling": "logic",
        "race_condition": "logic",
    }

    def __init__(self, llm_factory: LLMFactory):
        self.llm_factory = llm_factory

    def _load_dataset(self) -> list[dict]:
        """Load the golden dataset from JSON."""
        dataset_path = os.path.join(os.path.dirname(__file__), "golden_reviews.json")
        if not os.path.exists(dataset_path):
            raise FileNotFoundError(f"Eval dataset not found: {dataset_path}")
        with open(dataset_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _get_reviewer_for_category(self, category: str):
        """Get the appropriate reviewer for a finding category."""
        reviewer_name = self.CATEGORY_MAPPING.get(category, "logic")

        from server.ai.reviewers.security import SecurityReviewer
        from server.ai.reviewers.performance import PerformanceReviewer
        from server.ai.reviewers.logic import LogicReviewer
        from server.ai.reviewers.style import StyleReviewer

        reviewers = {
            "security": SecurityReviewer(self.llm_factory),
            "performance": PerformanceReviewer(self.llm_factory),
            "logic": LogicReviewer(self.llm_factory),
            "style": StyleReviewer(self.llm_factory),
        }
        return reviewers.get(reviewer_name)

    def _match_finding(self, actual: dict, expected: dict, file_path: str) -> bool:
        """Check if an actual finding matches an expected finding.

        Uses category + approximate line match (within 5 lines).
        """
        if actual.get("category") != expected.get("category"):
            return False

        expected_line = expected.get("line", 0)
        actual_line = actual.get("line_start", 0) or actual.get("line", 0)

        if abs(actual_line - expected_line) <= 5:
            return True

        # Also check file path
        actual_file = actual.get("file_path", "")
        if actual_file and file_path and file_path in actual_file:
            if abs(actual_line - expected_line) <= 10:
                return True

        return False

    async def run_eval(self) -> EvalReport:
        """Load dataset, run reviews, and compute precision/recall."""
        dataset = self._load_dataset()
        results: list[EvalTestResult] = []

        for case in dataset:
            test_id = case["test_id"]
            file_path = case.get("file_path", "")
            diff_chunk = case.get("diff_chunk", "")
            language = case.get("language", "python")
            expected = case.get("expected_findings", [])
            should_not = set(case.get("should_not_find", []))

            logger.info("Eval running: %s — %s", test_id, case.get("description", ""))

            try:
                # Build a chunk dict
                chunk = {
                    "file_path": file_path,
                    "content": diff_chunk,
                    "context": {},
                }

                # Get the expected reviewer
                reviewer_name = expected[0]["reviewer"] if expected else "logic"
                reviewer = self._get_reviewer_for_category(
                    expected[0]["category"] if expected else "other"
                )

                if not reviewer:
                    results.append(EvalTestResult(
                        test_id=test_id,
                        description=case.get("description", ""),
                        expected_count=len(expected),
                        actual_count=0,
                        true_positives=0,
                        false_positives=0,
                        false_negatives=len(expected),
                        precision=0.0,
                        recall=0.0,
                        f1=0.0,
                    ))
                    continue

                # Run the reviewer
                actual_findings = await reviewer.review(chunk)

                # Compute matches
                matched_expected = set()
                matched_actual = set()

                for ei, exp in enumerate(expected):
                    for ai, act in enumerate(actual_findings):
                        if ai in matched_actual:
                            continue
                        if self._match_finding(act, exp, file_path):
                            matched_expected.add(ei)
                            matched_actual.add(ai)
                            break

                true_positives = len(matched_expected)
                false_positives = len(actual_findings) - len(matched_actual)
                false_negatives = len(expected) - true_positives

                precision = true_positives / len(actual_findings) if actual_findings else 0.0
                recall = true_positives / len(expected) if expected else 1.0
                f1 = (
                    2 * precision * recall / (precision + recall)
                    if (precision + recall) > 0
                    else 0.0
                )

                # Build detail list
                details = []
                for act in actual_findings:
                    is_match = any(
                        self._match_finding(act, exp, file_path)
                        for exp in expected
                    )
                    details.append({
                        "title": act.get("title", ""),
                        "category": act.get("category", ""),
                        "severity": act.get("severity", ""),
                        "line": act.get("line_start", 0),
                        "matched": is_match,
                    })

                results.append(EvalTestResult(
                    test_id=test_id,
                    description=case.get("description", ""),
                    expected_count=len(expected),
                    actual_count=len(actual_findings),
                    true_positives=true_positives,
                    false_positives=false_positives,
                    false_negatives=false_negatives,
                    precision=round(precision, 2),
                    recall=round(recall, 2),
                    f1=round(f1, 2),
                    details=details,
                ))

                logger.info(
                    "Eval result %s: P=%.2f R=%.2f F1=%.2f (TP=%d FP=%d FN=%d)",
                    test_id, precision, recall, f1,
                    true_positives, false_positives, false_negatives,
                )

            except Exception as e:
                logger.error("Eval failed for %s: %s", test_id, e, exc_info=True)
                results.append(EvalTestResult(
                    test_id=test_id,
                    description=case.get("description", ""),
                    expected_count=len(expected),
                    actual_count=0,
                    true_positives=0,
                    false_positives=0,
                    false_negatives=len(expected),
                    precision=0.0,
                    recall=0.0,
                    f1=0.0,
                ))

        # Build report
        avg_precision = statistics.mean([r.precision for r in results]) if results else 0.0
        avg_recall = statistics.mean([r.recall for r in results]) if results else 0.0
        avg_f1 = statistics.mean([r.f1 for r in results]) if results else 0.0

        total_tp = sum(r.true_positives for r in results)
        total_fp = sum(r.false_positives for r in results)
        total_fn = sum(r.false_negatives for r in results)

        if avg_f1 >= 0.8:
            summary = (
                f"Good performance: avg F1={avg_f1:.2f}, avg P={avg_precision:.2f}, "
                f"avg R={avg_recall:.2f}. Total: {total_tp} TP, {total_fp} FP, {total_fn} FN."
            )
        elif avg_f1 >= 0.6:
            summary = (
                f"Moderate performance: avg F1={avg_f1:.2f}. "
                f"Consider tuning prompts to improve. FP={total_fp}, FN={total_fn}."
            )
        else:
            summary = (
                f"Needs improvement: avg F1={avg_f1:.2f}. "
                f"Review prompts and model selection. FP={total_fp}, FN={total_fn}."
            )

        return EvalReport(
            total_tests=len(results),
            avg_precision=round(avg_precision, 2),
            avg_recall=round(avg_recall, 2),
            avg_f1=round(avg_f1, 2),
            results=results,
            summary=summary,
        )
