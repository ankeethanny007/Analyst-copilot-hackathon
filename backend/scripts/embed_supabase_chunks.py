"""Create OpenAI embeddings for a loaded filing and persist them to pgvector."""

import json
import os
import ssl
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import certifi

DOCUMENT_ID = os.environ["DEMO_DOCUMENT_ID"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def call(url: str, method: str, headers: dict, payload=None):
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers, method=method)
    for attempt in range(6):
        try:
            with urlopen(request, timeout=90, context=SSL_CONTEXT) as response:
                return response.read()
        except HTTPError as error:
            if error.code != 429 or attempt == 5:
                raise RuntimeError(f"{method} {url}: {error.code} {error.read().decode()}") from error
            time.sleep(2 ** attempt)


def main() -> None:
    query = urlencode({"document_id": f"eq.{DOCUMENT_ID}", "select": "id,document_id,section_id,page_number,content,content_type", "order": "id"})
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    chunks = json.loads(call(f"{SUPABASE_URL}/document_chunks?{query}", "GET", headers))
    for start in range(0, len(chunks), 10):
        batch = chunks[start : start + 10]
        response = json.loads(call("https://api.openai.com/v1/embeddings", "POST", {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}, {"model": MODEL, "input": [row["content"] for row in batch], "dimensions": 1536}))
        for row, embedding in zip(batch, response["data"]):
            row["embedding"] = embedding["embedding"]
        call(f"{SUPABASE_URL}/document_chunks?on_conflict=id", "POST", {**headers, "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates,return=minimal"}, batch)
        print(f"Embedded {min(start + len(batch), len(chunks))}/{len(chunks)} chunks")
        time.sleep(0.25)
    call(f"{SUPABASE_URL}/documents?id=eq.{DOCUMENT_ID}", "PATCH", {**headers, "Content-Type": "application/json", "Prefer": "return=minimal"}, {"status": "ready", "processed_at": datetime.now(timezone.utc).isoformat()})
    call(f"{SUPABASE_URL}/processing_jobs?document_id=eq.{DOCUMENT_ID}", "PATCH", {**headers, "Content-Type": "application/json", "Prefer": "return=minimal"}, {"status": "ready", "stage": "complete", "progress": 100, "updated_at": datetime.now(timezone.utc).isoformat()})
    print(f"Ready: {DOCUMENT_ID} ({len(chunks)} chunks, {MODEL})")


if __name__ == "__main__":
    main()
