"""Offline scoring helpers for the supplied analyst-question benchmark.

The scoring mirrors the BRD: correct answer *and* correct page earns +1;
abstention/insufficient support earns 0; a supported but wrong answer earns
-1.  The evaluator records the raw answer and cited pages so ambiguous
semantic cases can be reviewed instead of silently treated as a success.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _numbers(value: str) -> list[Decimal]:
    values: list[Decimal] = []
    for match in re.finditer(r"(?<![a-zA-Z])\$?\s*\(?([\d][\d,]*(?:\.\d+)?)\)?", value):
        try:
            values.append(Decimal(match.group(1).replace(",", "")))
        except InvalidOperation:
            continue
    return values


def answer_matches(expected: str, actual: str) -> bool:
    """Conservative automatic comparison; raw text remains in the report."""
    expected_normalized = _normalise(expected)
    actual_normalized = _normalise(actual)
    if expected_normalized and expected_normalized in actual_normalized:
        return True
    # Financial values can differ only in comma/decimal formatting. Only use
    # this fallback for concise numeric targets, never prose explanations.
    expected_numbers = _numbers(expected)
    actual_numbers = _numbers(actual)
    if len(expected_numbers) == 1 and expected_numbers[0] != 0 and actual_numbers:
        target = expected_numbers[0]
        return any(abs(value - target) <= max(Decimal("0.01"), abs(target) * Decimal("0.001")) for value in actual_numbers)
    return False


@dataclass(frozen=True)
class BenchmarkOutcome:
    financebench_id: str
    doc_name: str
    question: str
    question_type: str | None
    question_reasoning: str | None
    expected_answer: str
    expected_pages: tuple[int, ...]
    status: str
    actual_answer: str
    cited_pages: tuple[int, ...]
    answer_match: bool
    page_match: bool
    score: int
    latency_ms: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["expected_pages"] = list(self.expected_pages)
        value["cited_pages"] = list(self.cited_pages)
        return value


def score_case(case: dict[str, Any], *, status: str, actual_answer: str, cited_pages: list[int] | tuple[int, ...], latency_ms: int | None = None, error: str | None = None) -> BenchmarkOutcome:
    expected_pages = tuple(sorted({int(item["evidence_page_num"]) for item in case.get("evidence", []) if item.get("evidence_page_num") is not None}))
    actual_pages = tuple(sorted({int(page) for page in cited_pages if page is not None}))
    matched_answer = status == "supported" and answer_matches(case.get("answer", ""), actual_answer)
    matched_pages = bool(expected_pages) and set(expected_pages).issubset(actual_pages)
    if status != "supported":
        score = 0
    elif matched_answer and matched_pages:
        score = 1
    elif matched_answer:
        score = 0
    else:
        score = -1
    return BenchmarkOutcome(
        financebench_id=str(case.get("financebench_id", "")),
        doc_name=str(case.get("doc_name", "")),
        question=str(case.get("question", "")),
        question_type=case.get("question_type"),
        question_reasoning=case.get("question_reasoning"),
        expected_answer=str(case.get("answer", "")),
        expected_pages=expected_pages,
        status=status,
        actual_answer=actual_answer,
        cited_pages=actual_pages,
        answer_match=matched_answer,
        page_match=matched_pages,
        score=score,
        latency_ms=latency_ms,
        error=error,
    )


def summarize(outcomes: list[BenchmarkOutcome]) -> dict[str, Any]:
    """Return a compact, JSON-serializable diagnostic summary."""
    by_status = Counter(outcome.status for outcome in outcomes)
    by_question_type = Counter(outcome.question_type or "unknown" for outcome in outcomes)
    by_score = Counter(outcome.score for outcome in outcomes)
    failures = [outcome.to_dict() for outcome in outcomes if outcome.score != 1]
    return {
        "evaluated": len(outcomes),
        "score": sum(outcome.score for outcome in outcomes),
        "correct_with_location": by_score[1],
        "correct_wrong_location": sum(1 for outcome in outcomes if outcome.answer_match and not outcome.page_match),
        "abstentions": sum(1 for outcome in outcomes if outcome.status == "not_found"),
        "unsupported_or_wrong_supported": by_score[-1],
        "by_status": dict(by_status),
        "by_question_type": dict(by_question_type),
        "failures": failures,
    }
