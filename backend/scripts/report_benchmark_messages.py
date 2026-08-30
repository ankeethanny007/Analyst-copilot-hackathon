#!/usr/bin/env python3
"""Score benchmark questions that were already asked through the real UI.

Unlike ``evaluate_benchmark.py``, this utility never submits a question.  It
correlates persisted user/assistant message pairs with the supplied benchmark
and captures the UI-driven answer, source attachment, and source snapshots in
one ignored local report.

Example:
  PYTHONPATH=.:backend python backend/scripts/report_benchmark_messages.py \
    --api-base http://127.0.0.1:8007 \
    --output Files/benchmark-results/ui-report.json \
    --require-all
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import ssl
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi

from backend.app.services.benchmark_evaluation import BenchmarkOutcome, score_case, summarize


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "sample-data" / "practice-questions.jsonl"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def normalise_filename(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", Path(value).stem.lower())


def request_json(url: str, *, timeout: int = 60) -> Any:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            return json.loads(response.read() or b"null")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1200]
        raise RuntimeError(f"GET {url} failed ({error.code}): {detail}") from error
    except URLError as error:
        raise RuntimeError(f"GET {url} could not connect: {error.reason}") from error


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evidence_pages(message: dict[str, Any], page_offset: int) -> list[int]:
    pages: list[int] = []
    for item in message.get("message_evidence") or []:
        try:
            pages.append(int(item.get("page_number")) - page_offset)
        except (TypeError, ValueError):
            pass
    return pages


def _date_after(value: str | None, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed >= cutoff


def latest_answer_for_question(messages: list[dict[str, Any]], question: str, since: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
    """Return the latest matching user message and its following assistant reply.

    A later user turn ends the candidate pair.  This makes reports stable even
    when a benchmark document has ordinary demo chat history mixed in.
    """
    candidate: tuple[dict[str, Any], dict[str, Any] | None] | None = None
    for index, message in enumerate(messages):
        if message.get("role") != "user" or message.get("content") != question or not _date_after(message.get("created_at"), since):
            continue
        assistant: dict[str, Any] | None = None
        for next_message in messages[index + 1 :]:
            if next_message.get("role") == "user":
                break
            if next_message.get("role") == "assistant":
                assistant = next_message
                break
        candidate = (message, assistant)
    return candidate


def evidence_snapshot(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep concise source metadata for manual UI/evidence review."""
    return [
        {
            "page_number": item.get("page_number"),
            "heading": item.get("table_title") or item.get("section_heading"),
            "source_type": item.get("source_type"),
            "excerpt": str(item.get("excerpt") or "")[:1000],
        }
        for item in message.get("message_evidence") or []
    ]


def format_check(message: dict[str, Any]) -> dict[str, bool]:
    status = str(message.get("answer_status") or "failed")
    content = str(message.get("content") or "")
    evidence = message.get("message_evidence") or []
    citations = bool(re.search(r"\[S\d+\]", content))
    return {
        "supported_has_citation": status != "supported" or citations,
        "supported_has_sources": status != "supported" or bool(evidence),
        "non_supported_has_no_sources": status == "supported" or not evidence,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000", help="FastAPI origin, without /v1")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--page-offset", type=int, default=0)
    parser.add_argument("--since", help="Only use user messages created at/after this ISO-8601 timestamp")
    parser.add_argument("--require-all", action="store_true", help="Fail when a filing/topic/question is absent from the UI history")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since else None
    except ValueError:
        print("--since must be ISO-8601", file=sys.stderr)
        return 2
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    base = args.api_base.rstrip("/") + "/v1"
    try:
        documents = request_json(f"{base}/documents")
        topics = request_json(f"{base}/chat-topics")
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    documents_by_name = {normalise_filename(str(document.get("original_filename") or "")): document for document in documents or [] if document.get("status") == "ready"}
    topics_by_document: dict[str, list[dict[str, Any]]] = {}
    for topic in topics or []:
        topics_by_document.setdefault(str(topic.get("document_id")), []).append(topic)

    messages_by_topic: dict[str, list[dict[str, Any]]] = {}
    outcomes: list[BenchmarkOutcome] = []
    reviewed: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for case in load_cases(args.dataset):
        document = documents_by_name.get(normalise_filename(str(case.get("doc_name") or "")))
        if not document:
            missing.append({"financebench_id": str(case.get("financebench_id") or ""), "reason": "filing is not ready"})
            continue
        candidate_topics = topics_by_document.get(str(document.get("id")), [])
        if not candidate_topics:
            missing.append({"financebench_id": str(case.get("financebench_id") or ""), "reason": "filing has no chat topic"})
            continue
        matching: tuple[dict[str, Any], dict[str, Any] | None] | None = None
        for topic in candidate_topics:
            topic_id = str(topic["id"])
            if topic_id not in messages_by_topic:
                messages_by_topic[topic_id] = request_json(f"{base}/chat-topics/{topic_id}/messages")
            found = latest_answer_for_question(messages_by_topic[topic_id], str(case.get("question") or ""), since)
            if found and (matching is None or str(found[0].get("created_at") or "") >= str(matching[0].get("created_at") or "")):
                matching = found
        if matching is None:
            missing.append({"financebench_id": str(case.get("financebench_id") or ""), "reason": "question was not found in UI history"})
            continue

        user_message, assistant_message = matching
        if assistant_message is None:
            outcome = score_case(case, status="failed", actual_answer="", cited_pages=[], error="UI user message has no following assistant reply")
            reviewed.append({"financebench_id": outcome.financebench_id, "user_message_id": user_message.get("id"), "assistant_message_id": None, "format": {}})
        else:
            outcome = score_case(
                case,
                status=str(assistant_message.get("answer_status") or "failed"),
                actual_answer=str(assistant_message.get("content") or ""),
                cited_pages=evidence_pages(assistant_message, args.page_offset),
            )
            reviewed.append(
                {
                    "financebench_id": outcome.financebench_id,
                    "user_message_id": user_message.get("id"),
                    "assistant_message_id": assistant_message.get("id"),
                    "created_at": assistant_message.get("created_at"),
                    "format": format_check(assistant_message),
                    "evidence": evidence_snapshot(assistant_message),
                }
            )
        outcomes.append(outcome)

    report = summarize(outcomes)
    report.update(
        {
            "mode": "persisted-ui-messages",
            "page_offset": args.page_offset,
            "since": args.since,
            "missing": missing,
            "reviewed": reviewed,
            "outcomes": [outcome.to_dict() for outcome in outcomes],
            "format_failures": {
                key: sum(not item["format"].get(key, False) for item in reviewed if item.get("format"))
                for key in ("supported_has_citation", "supported_has_sources", "non_supported_has_no_sources")
            },
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "evaluated": report["evaluated"], "missing": len(missing), "score": report["score"]}))
    if args.require_all and (len(outcomes) != len(load_cases(args.dataset)) or missing):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
