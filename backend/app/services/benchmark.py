"""Read-only loader for the bundled FinanceBench-style evaluation set.

This module is deliberately **not** part of the live chat path.  The supplied
answers/pages are development and scoring data: runtime answers must come from
the active filing's own retrieved evidence, including paraphrased questions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkCase:
    answer: str
    evidence_phrase: str
    evidence_heading: str
    evidence_page: int | None


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _heading(question: str) -> str:
    question = question.lower()
    if "cash flow" in question:
        return "Consolidated Statement of Cash Flows"
    if "balance sheet" in question or "statement of financial position" in question:
        return "Consolidated Balance Sheet"
    if "income statement" in question or "p&l" in question:
        return "Consolidated Statement of Income"
    return "Filing section"


def _evidence_phrase(row: dict) -> str:
    justification = row.get("justification") or ""
    match = re.search(r"line item name, as seen in the .*? was:\s*(.+?)(?:\.|$)", justification, re.I | re.S)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).replace("â", "—").strip()
    text = row.get("evidence", [{}])[0].get("evidence_text", "")
    return " ".join(re.findall(r"[A-Za-z][A-Za-z -]{4,}", text)[:1])


@lru_cache(maxsize=1)
def _cases() -> dict[tuple[str, str], BenchmarkCase]:
    path = Path(__file__).resolve().parents[3] / "sample-data" / "practice-questions.jsonl"
    cases: dict[tuple[str, str], BenchmarkCase] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        evidence = row.get("evidence", [{}])[0]
        cases[(_normalise(row["doc_name"]), _normalise(row["question"]))] = BenchmarkCase(
            answer=row["answer"], evidence_phrase=_evidence_phrase(row), evidence_heading=_heading(row["question"]), evidence_page=evidence.get("evidence_page_num")
        )
    return cases


def benchmark_case(filename: str, question: str) -> BenchmarkCase | None:
    """Find an offline evaluation case by filename/question.

    Kept for the benchmark tests and evaluator; production routes must not
    call it or inject its answer into a chat response.
    """
    return _cases().get((_normalise(Path(filename).stem), _normalise(question)))
