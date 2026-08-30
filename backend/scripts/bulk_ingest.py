#!/usr/bin/env python3
"""Resumable, sequential ingestion for the local FinanceBench filing corpus.

This utility deliberately uses the public Analyst Copilot API instead of
writing directly to Supabase.  Each file therefore follows the production
path: original filing -> R2 -> document/topic/job -> processing -> ready.
It never deletes records or source files.

Example:
  PYTHONPATH=. python backend/scripts/bulk_ingest.py \
    --api-base http://127.0.0.1:8006 \
    --source-dir Files
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

import certifi


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = REPO_ROOT / "sample-data" / "practice-questions.jsonl"
DEFAULT_SOURCE_DIR = REPO_ROOT / "Files"
DEFAULT_STATE_PATH = DEFAULT_SOURCE_DIR / "benchmark-results" / "bulk-ingest-state.json"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise_filename(value: str) -> str:
    return "".join(character for character in Path(value).stem.lower() if character.isalnum())


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expected_document_names(cases: Iterable[dict[str, Any]]) -> list[str]:
    """Preserve benchmark order while selecting each filing exactly once."""
    seen: set[str] = set()
    names: list[str] = []
    for case in cases:
        name = str(case.get("doc_name") or "").strip()
        key = normalise_filename(name)
        if not name or not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def resolve_files(source_dir: Path, document_names: Iterable[str]) -> tuple[dict[str, Path], list[str]]:
    """Map expected benchmark names to unique local HTML files.

    Matching uses a filename stem rather than a path so the original corpus can
    remain a flat, ignored folder.  Ambiguous normalized names are reported as
    errors rather than selecting an arbitrary filing.
    """
    candidates: dict[str, list[Path]] = {}
    for path in sorted(source_dir.glob("*")):
        if path.is_file() and path.suffix.lower() in {".htm", ".html", ".xhtml"}:
            candidates.setdefault(normalise_filename(path.name), []).append(path)

    resolved: dict[str, Path] = {}
    errors: list[str] = []
    for name in document_names:
        matches = candidates.get(normalise_filename(name), [])
        if not matches:
            errors.append(f"Missing local filing for benchmark document: {name}")
        elif len(matches) > 1:
            rendered = ", ".join(str(path) for path in matches)
            errors.append(f"Ambiguous local filings for {name}: {rendered}")
        else:
            resolved[name] = matches[0]
    return resolved, errors


def _http_json(url: str, *, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 180) -> Any:
    request_headers = {"Accept": "application/json", **(headers or {})}
    request = Request(url, data=body, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=timeout, context=SSL_CONTEXT) as response:
            payload = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1500]
        raise RuntimeError(f"{method} {url} failed ({error.code}): {detail}") from error
    except URLError as error:
        raise RuntimeError(f"{method} {url} could not connect: {error.reason}") from error
    return json.loads(payload) if payload else None


def _multipart_payload(path: Path) -> tuple[bytes, str]:
    boundary = f"----AnalystCopilot{uuid4().hex}"
    media_type = "application/xhtml+xml" if path.suffix.lower() == ".xhtml" else "text/html"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {media_type}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + path.read_bytes() + footer, boundary


def upload_document(api_base: str, path: Path, timeout: int) -> dict[str, Any]:
    payload, boundary = _multipart_payload(path)
    response = _http_json(
        f"{api_base.rstrip('/')}/v1/documents",
        method="POST",
        body=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=timeout,
    )
    if not isinstance(response, dict) or not isinstance(response.get("document"), dict):
        raise RuntimeError("Upload response did not contain a document")
    return response


def document_status(api_base: str, document_id: str, timeout: int) -> dict[str, Any]:
    response = _http_json(f"{api_base.rstrip('/')}/v1/documents/{document_id}/status", timeout=timeout)
    if not isinstance(response, dict) or not isinstance(response.get("document"), dict):
        raise RuntimeError("Status response did not contain a document")
    return response


def wait_for_ready(api_base: str, document_id: str, *, poll_interval: float, max_wait_seconds: float, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + max_wait_seconds
    latest: dict[str, Any] = {}
    while True:
        latest = document_status(api_base, document_id, timeout)
        document = latest["document"]
        status = str(document.get("status") or "")
        if status in {"ready", "failed"}:
            return latest
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Processing timed out after {max_wait_seconds:.0f}s (last status: {status or 'unknown'})")
        time.sleep(poll_interval)


def _load_state(path: Path, *, api_base: str, source_dir: Path, dataset: Path) -> dict[str, Any]:
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("documents"), dict):
                return value
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "version": 1,
        "started_at": now(),
        "api_base": api_base,
        "source_dir": str(source_dir),
        "dataset": str(dataset),
        "documents": {},
    }


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000", help="FastAPI origin, without /v1")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH, help="Ignored local progress report")
    parser.add_argument("--limit-documents", type=int, default=0, help="For a small smoke batch; 0 uploads every benchmark filing")
    parser.add_argument("--dry-run", action="store_true", help="Validate the corpus mapping without calling the API")
    parser.add_argument("--poll-interval", type=float, default=4.0)
    parser.add_argument("--max-wait-seconds", type=float, default=1800.0)
    parser.add_argument("--request-timeout", type=int, default=180)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(args.dataset)
    documents = expected_document_names(cases)
    if not documents:
        print("No benchmark documents found.", file=sys.stderr)
        return 2
    if args.limit_documents > 0:
        documents = documents[: args.limit_documents]
    resolved, errors = resolve_files(args.source_dir, documents)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2

    plan = {
        "benchmark_questions": len(cases),
        "selected_documents": len(documents),
        "source_dir": str(args.source_dir),
        "files": [{"document": name, "path": str(resolved[name]), "bytes": resolved[name].stat().st_size} for name in documents],
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0

    health = _http_json(f"{args.api_base.rstrip('/')}/health", timeout=args.request_timeout)
    if health != {"status": "ok"}:
        print(f"Unexpected API health response: {health!r}", file=sys.stderr)
        return 2

    state = _load_state(args.state_file, api_base=args.api_base, source_dir=args.source_dir, dataset=args.dataset)
    # A prior completed smoke batch may share this checkpoint. Mark the
    # resumed run honestly while preserving its document-level history.
    state.pop("finished_at", None)
    state.pop("summary", None)
    state.update({"last_started_at": now(), "api_base": args.api_base, "source_dir": str(args.source_dir), "dataset": str(args.dataset)})
    _write_state(args.state_file, state)
    records: dict[str, dict[str, Any]] = state["documents"]
    failures = 0
    for index, name in enumerate(documents, start=1):
        path = resolved[name]
        record = records.setdefault(name, {})
        record.update({"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "started_at": now()})
        _write_state(args.state_file, state)
        try:
            response = upload_document(args.api_base, path, args.request_timeout)
            document = response["document"]
            document_id = str(document["id"])
            record.update({"document_id": document_id, "deduplicated": bool(response.get("deduplicated")), "upload_complete_at": now()})
            _write_state(args.state_file, state)
            snapshot = wait_for_ready(
                args.api_base,
                document_id,
                poll_interval=args.poll_interval,
                max_wait_seconds=args.max_wait_seconds,
                timeout=args.request_timeout,
            )
            current = snapshot["document"]
            job = snapshot.get("job") or {}
            record.update(
                {
                    "status": current.get("status"),
                    "processed_at": current.get("processed_at"),
                    "processing_error": current.get("processing_error") or job.get("error"),
                    "completed_at": now(),
                }
            )
            if current.get("status") != "ready":
                failures += 1
            print(f"[{index}/{len(documents)}] {name}: {current.get('status', 'unknown')}")
        except (RuntimeError, TimeoutError) as error:
            failures += 1
            record.update({"status": "failed", "processing_error": str(error), "completed_at": now()})
            print(f"[{index}/{len(documents)}] {name}: failed — {error}", file=sys.stderr)
        _write_state(args.state_file, state)

    state["finished_at"] = now()
    state["summary"] = {
        "selected_documents": len(documents),
        "ready": sum(record.get("status") == "ready" for record in records.values()),
        "failed": failures,
    }
    _write_state(args.state_file, state)
    print(json.dumps({"state_file": str(args.state_file), **state["summary"]}))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
