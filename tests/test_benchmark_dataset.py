import json
from collections import Counter
from pathlib import Path


BENCHMARK = Path("sample-data/practice-questions.jsonl")
REQUIRED_FIELDS = {"financebench_id", "company", "doc_name", "question", "answer", "evidence"}


def test_practice_benchmark_is_well_formed() -> None:
    rows = [json.loads(line) for line in BENCHMARK.read_text().splitlines() if line.strip()]
    assert len(rows) == 136
    assert all(REQUIRED_FIELDS <= row.keys() for row in rows)
    assert all(row["evidence"] and row["evidence"][0]["evidence_page_num"] is not None for row in rows)
    assert len({row["financebench_id"] for row in rows}) == len(rows)


def test_benchmark_documents_are_identifiable() -> None:
    rows = [json.loads(line) for line in BENCHMARK.read_text().splitlines() if line.strip()]
    document_counts = Counter(row["doc_name"] for row in rows)
    assert document_counts
    assert all(count > 0 for count in document_counts.values())
