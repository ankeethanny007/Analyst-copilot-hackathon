#!/usr/bin/env python3
"""Run the supplied practice questions against already-ingested filings.

This is an offline developer/evaluation command.  It never gives the expected
answer to the API; it only sends the question, records the returned answer and
evidence pages, then compares the result locally after the request finishes.

Example:
  PYTHONPATH=. python backend/scripts/evaluate_benchmark.py \
    --api-base http://127.0.0.1:8000 --output /tmp/benchmark-report.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.app.services.benchmark_evaluation import BenchmarkOutcome, score_case, summarize


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "sample-data" / "practice-questions.jsonl"


def normalise_filename(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", Path(value).stem.lower())


def request_json(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, bearer_token: str | None = None) -> Any:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = Request(url, method=method, data=data, headers=headers)
    with urlopen(request, timeout=180) as response:
        return json.loads(response.read() or b"null")


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evidence_pages(payload: dict[str, Any], page_offset: int = 0) -> list[int]:
    pages: list[int] = []
    for item in payload.get("evidence") or []:
        value = item.get("page_number")
        if value is not None:
            try:
                pages.append(int(value) - page_offset)
            except (TypeError, ValueError):
                pass
    return pages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000", help="FastAPI origin, without /v1")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True, help="JSON report destination")
    parser.add_argument("--bearer-token", default=os.getenv("BENCHMARK_BEARER_TOKEN"))
    parser.add_argument("--document", help="Optional filing filename/stem to evaluate")
    parser.add_argument("--limit", type=int, default=0, help="Maximum eligible questions (0 = all)")
    parser.add_argument(
        "--page-offset",
        type=int,
        default=0,
        help="Subtract this offset from displayed source pages before comparison (use 1 when a benchmark stores zero-based PDF pages but the HTML parser displays page 1 first).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report eligible questions without sending chat requests")
    args = parser.parse_args()

    base = args.api_base.rstrip("/") + "/v1"
    try:
        documents = request_json(f"{base}/documents", bearer_token=args.bearer_token)
        topics = request_json(f"{base}/chat-topics", bearer_token=args.bearer_token)
    except (HTTPError, URLError, TimeoutError) as error:
        print(f"Unable to load filing library: {error}", file=sys.stderr)
        return 2

    document_by_stem = {normalise_filename(document["original_filename"]): document for document in documents if document.get("status") == "ready"}
    topic_by_document: dict[str, dict[str, Any]] = {}
    for topic in topics:
        topic_by_document.setdefault(str(topic.get("document_id")), topic)

    requested_document = normalise_filename(args.document) if args.document else None
    eligible = [
        case
        for case in load_cases(args.dataset)
        if (not requested_document or normalise_filename(case["doc_name"]) == requested_document)
        and normalise_filename(case["doc_name"]) in document_by_stem
        and str(document_by_stem[normalise_filename(case["doc_name"])].get("id")) in topic_by_document
    ]
    if args.limit > 0:
        eligible = eligible[: args.limit]
    if args.dry_run:
        print(json.dumps({"eligible": len(eligible), "documents": sorted({case["doc_name"] for case in eligible})}, indent=2))
        return 0

    outcomes: list[BenchmarkOutcome] = []
    for index, case in enumerate(eligible, start=1):
        document = document_by_stem[normalise_filename(case["doc_name"])]
        topic = topic_by_document[str(document["id"])]
        started = time.perf_counter()
        try:
            response = request_json(
                f"{base}/chat-topics/{topic['id']}/messages",
                method="POST",
                body={"content": case["question"]},
                bearer_token=args.bearer_token,
            )
            assistant = response.get("assistant_message") or {}
            outcome = score_case(
                case,
                status=assistant.get("answer_status") or response.get("answer_status") or "failed",
                actual_answer=assistant.get("content") or "",
                cited_pages=evidence_pages(response, args.page_offset),
                latency_ms=round((time.perf_counter() - started) * 1000),
            )
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            outcome = score_case(
                case,
                status="failed",
                actual_answer="",
                cited_pages=[],
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=str(error),
            )
        outcomes.append(outcome)
        print(f"[{index}/{len(eligible)}] {case['doc_name']} {outcome.score:+d} {outcome.status}")

    report = summarize(outcomes)
    report["outcomes"] = [outcome.to_dict() for outcome in outcomes]
    report["eligible_documents"] = sorted({case["doc_name"] for case in eligible})
    report["page_offset"] = args.page_offset
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}: score {report['score']} across {report['evaluated']} eligible questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
